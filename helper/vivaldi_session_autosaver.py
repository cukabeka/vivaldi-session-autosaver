#!/usr/bin/env python3
"""Vivaldi Session Autosaver — helper core.

Snapshots Vivaldi's profile Sessions directory (SNSS files) into dated
backups under ~/.vivaldi-session-autosaver/snapshots/, applies retention,
and maintains a status.json consumed by the companion browser extension.

Python 3 stdlib only. macOS only (MVP).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "vivaldi-session-autosaver"
STATE_DIR = Path.home() / ".vivaldi-session-autosaver"
SNAPSHOTS_DIR = STATE_DIR / "snapshots"
STATUS_FILE = STATE_DIR / "status.json"
REPORT_FILE = STATE_DIR / "recovery_report.html"
CONFIG_FILE = STATE_DIR / "config.json"
SCHEMA_VERSION = 1
HELPER_VERSION = "0.2.0"

# Per-snapshot manifest recording each stored file (size/mtime) so unchanged
# files can be hardlinked from the previous snapshot instead of re-compressed.
MANIFEST_NAME = ".manifest.json"

# Sentinel file that records which snapshot fingerprint the HTML report was
# last built from — avoids rebuilding when nothing changed.
REPORT_FINGERPRINT_FILE = STATE_DIR / ".report_fingerprint"

# Default disk budget for all snapshots combined, in MB. 0 = unlimited
# (GFS retention only). Configurable via the extension popup or CLI.
DEFAULT_MAX_DISK_MB = 0
DEFAULT_INTERVAL_MIN = 15

DEFAULT_VIVALDI_PROFILE = (
    Path.home() / "Library" / "Application Support" / "Vivaldi" / "Default"
)

# Files/dirs that bloat snapshots without helping recovery:
# - Backup_after_crash/: Vivaldi's own crash backups (old, huge, duplicated)
# - *.zip: user-made manual archives
# - .DS_Store, .com.vivaldi.*: macOS/Vivaldi noise
EXCLUDE_PATTERNS = [
    "Backup_after_crash",
    "*.zip",
    ".DS_Store",
    ".com.vivaldi.*",
]

# SNSS files compress extremely well (~87%); gzip everything we copy.
GZIP_SUFFIX = ".gz"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_sessions_dir(profile_dir: Path | None) -> Path:
    """Locate the Vivaldi profile Sessions directory."""
    base = profile_dir or DEFAULT_VIVALDI_PROFILE
    sessions = base / "Sessions"
    if not sessions.is_dir():
        raise FileNotFoundError(
            f"Vivaldi Sessions directory not found: {sessions}\n"
            "Pass --profile-dir if you use a non-default profile."
        )
    return sessions


def is_excluded(rel_path: Path) -> bool:
    """True if a relative path inside Sessions/ should not be backed up."""
    parts = rel_path.parts
    for pat in EXCLUDE_PATTERNS:
        if any(fnmatch(part, pat) for part in parts):
            return True
    return False


def fnmatch(name: str, pat: str) -> bool:
    import fnmatch as _fm
    return _fm.fnmatch(name, pat)


def iter_backup_files(sessions_dir: Path):
    """Yield files to back up, applying exclusions."""
    for p in sorted(sessions_dir.rglob("*")):
        rel = p.relative_to(sessions_dir)
        if is_excluded(rel):
            continue
        if p.is_file():
            yield p, rel


def dir_fingerprint(sessions_dir: Path) -> str:
    """Cheap fingerprint of the Sessions dir (relative paths, size, mtime)."""
    h = hashlib.sha256()
    for p, rel in iter_backup_files(sessions_dir):
        st = p.stat()
        h.update(f"f:{rel}:{st.st_size}:{int(st.st_mtime)}\n".encode())
    return h.hexdigest()


def atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def load_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(**updates) -> dict:
    cfg = load_config()
    cfg.update(updates)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CONFIG_FILE, cfg)
    return cfg


def write_status(**updates) -> dict:
    status = load_status()
    status.update(
        {
            "schema": SCHEMA_VERSION,
            "version": HELPER_VERSION,
            "updated_at": utc_now_iso(),
        }
    )
    status.update(updates)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATUS_FILE, status)
    return status


def snapshot(sessions_dir: Path, keep: int) -> dict:
    """Copy Sessions/ (filtered, gzipped) into a new timestamped snapshot."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    fingerprint = dir_fingerprint(sessions_dir)

    # Skip if the newest snapshot already matches.
    existing = sorted(d for d in SNAPSHOTS_DIR.iterdir() if d.is_dir())
    if existing:
        fp_file = existing[-1] / ".fingerprint"
        if fp_file.exists() and fp_file.read_text().strip() == fingerprint:
            status = write_status(
                last_backup=existing[-1].name,
                snapshot_count=len(existing),
                total_bytes=dir_size(existing[-1]),
                snapshots=list_snapshots(),
                last_error=None,
                skipped_unchanged=True,
            )
            return status

    # Atomic staging: copy to a temp dir, then rename into place.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    staging = SNAPSHOTS_DIR / f".staging-{stamp}"
    dest = SNAPSHOTS_DIR / stamp

    # Hardlink dedup: unchanged files are linked from the previous snapshot
    # instead of re-compressed — near-zero CPU for the common case.
    prev_dir = existing[-1] if existing else None
    prev_manifest = _load_manifest(prev_dir) if prev_dir else {}
    manifest = {}
    try:
        for src, rel in iter_backup_files(sessions_dir):
            st = src.stat()
            key = str(rel)
            prev = prev_manifest.get(key)
            if (prev and prev["size"] == st.st_size
                    and prev["mtime"] == int(st.st_mtime)
                    and (prev_dir / prev["stored"]).exists()):
                stored = prev["stored"]
                target = staging / stored
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(prev_dir / stored, target)
                manifest[key] = {"size": st.st_size,
                                 "mtime": int(st.st_mtime),
                                 "stored": stored}
                continue
            if st.st_size > 4096:
                stored = f"{rel}{GZIP_SUFFIX}"
                out = staging / stored
                out.parent.mkdir(parents=True, exist_ok=True)
                # Level 6: ~2x faster than default 9, minimal size difference.
                with open(src, "rb") as fin, \
                        gzip.open(out, "wb", compresslevel=6) as fout:
                    shutil.copyfileobj(fin, fout)
            else:
                stored = str(rel)
                out = staging / stored
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, out)
            manifest[key] = {"size": st.st_size,
                             "mtime": int(st.st_mtime),
                             "stored": stored}
        _write_manifest(staging, manifest)
        (staging / ".fingerprint").write_text(fingerprint + "\n")
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(staging, dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    apply_retention(keep)
    enforce_disk_budget()
    existing = sorted(d for d in SNAPSHOTS_DIR.iterdir() if d.is_dir())
    status = write_status(
        last_backup=stamp,
        snapshot_count=len(existing),
        total_bytes=dir_size(dest),
        snapshots=list_snapshots(),
        last_error=None,
        skipped_unchanged=False,
    )
    return status


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _load_manifest(snap_dir: Path) -> dict:
    """Load the per-snapshot manifest tracking stored files."""
    try:
        return json.loads((snap_dir / MANIFEST_NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_manifest(staging: Path, manifest: dict) -> None:
    """Write the per-snapshot manifest next to the data files."""
    (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))


# GFS retention (Grandfather-Father-Son): keep the newest N snapshots per
# age bucket. Buckets are computed from snapshot age in hours.
GFS_BUCKETS = [
    ("hourly", 24),    # last 24 hours: one per hour
    ("daily", 7),      # last 7 days: one per day
    ("weekly", 4),     # last 4 weeks: one per week
]


def parse_snapshot_stamp(name: str) -> datetime | None:
    try:
        return datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def apply_retention(keep: int = 0) -> None:
    """GFS retention: keep newest snapshot per bucket, always keep newest.

    `keep` (flat limit) is honored as an upper bound for compatibility;
    GFS buckets decide what survives beyond the newest few.
    """
    snaps = []
    for d in SNAPSHOTS_DIR.iterdir():
        if d.is_dir():
            ts = parse_snapshot_stamp(d.name)
            if ts:
                snaps.append((ts, d))
    snaps.sort(reverse=True)
    if not snaps:
        return

    now = datetime.now(timezone.utc)
    keep_set = {snaps[0][1]}  # always keep the newest
    seen_buckets = set()
    for ts, d in snaps:
        age_h = (now - ts).total_seconds() / 3600
        if age_h < 1:
            bucket = "h0"
        elif age_h < 24:
            bucket = ("hourly", int(age_h))
        elif age_h < 24 * 7:
            bucket = ("daily", int(age_h // 24))
        elif age_h < 24 * 7 * 5:
            bucket = ("weekly", int(age_h // (24 * 7)))
        else:
            bucket = None  # older than 5 weeks → delete
        if bucket is None:
            continue
        if bucket not in seen_buckets:
            seen_buckets.add(bucket)
            keep_set.add(d)

    # Flat upper bound (if given) keeps the newest `keep` regardless.
    if keep > 0:
        for _ts, d in snaps[:keep]:
            keep_set.add(d)

    for _ts, d in snaps:
        if d not in keep_set:
            shutil.rmtree(d, ignore_errors=True)


def enforce_disk_budget(max_disk_mb: int | None = None) -> int:
    """Delete oldest snapshots until total size fits the budget.

    The newest snapshot is always kept. Returns bytes freed.
    """
    if max_disk_mb is None:
        max_disk_mb = load_config().get("max_disk_mb", DEFAULT_MAX_DISK_MB)
    if not max_disk_mb or max_disk_mb <= 0:
        return 0
    budget = max_disk_mb * 1024 * 1024

    snaps = sorted(
        (d for d in SNAPSHOTS_DIR.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    freed = 0
    total = 0
    sizes = {d: dir_size(d) for d in snaps}
    for i, d in enumerate(snaps):
        total += sizes[d]
        # Keep the newest (i == 0) even if it alone exceeds the budget.
        if total > budget and i > 0:
            freed += sizes[d]
            shutil.rmtree(d, ignore_errors=True)
    return freed


def list_snapshots() -> list:
    """Return snapshot metadata for the extension (newest first)."""
    result = []
    for d in SNAPSHOTS_DIR.iterdir():
        ts = parse_snapshot_stamp(d.name)
        if d.is_dir() and ts:
            result.append({
                "name": d.name,
                "captured_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bytes": dir_size(d),
            })
    result.sort(key=lambda s: s["captured_at"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# HTML recovery report (inspired by the Vivaldi forum recovery script)
# ---------------------------------------------------------------------------

def load_workspaces(profile_dir: Path) -> dict:
    """Load workspace id→name mapping from Vivaldi Preferences."""
    for name in ("Preferences.bak_exittype", "Preferences"):
        prefs_path = profile_dir / name
        if not prefs_path.exists():
            continue
        try:
            prefs = json.loads(prefs_path.read_text())
            ws_list = prefs.get("vivaldi", {}).get("workspaces", {}).get("list", [])
            ws_map = {}
            for ws in ws_list:
                wid = ws.get("id")
                if wid:
                    ws_map[wid] = ws.get("name", "Unknown")
                    ws_map[str(wid)] = ws.get("name", "Unknown")
            if ws_map:
                return ws_map
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def extract_tabs_from_snss(filepath: Path) -> list:
    """Extract tab records (url/title/workspace) from a SNSS .bin file.

    SNSS embeds JSON blobs per tab containing urlForThumbnail, fixedTitle
    and (Vivaldi) workspaceId. We scan for those blobs heuristically.
    """
    results = []
    try:
        data = gzip.open(filepath, "rb").read() if filepath.suffix == GZIP_SUFFIX \
            else filepath.read_bytes()
    except (OSError, gzip.BadGzipFile):
        return results

    for match in re.finditer(rb"\{[^\x00\x01\x02\x03]{20,5000}\}", data):
        raw = match.group()
        try:
            text = raw.decode("utf-8", errors="ignore")
            if '"urlForThumbnail"' not in text:
                continue
            for end_pos in range(len(text), max(10, len(text) - 500), -1):
                try:
                    obj = json.loads(text[:end_pos])
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    url = obj.get("urlForThumbnail", "")
                    if url.startswith("http"):
                        results.append({
                            "url": url,
                            "workspaceId": obj.get("workspaceId"),
                            "title": obj.get("fixedTitle", ""),
                        })
                    break
        except (UnicodeDecodeError, ValueError):
            continue
    return results


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;") \
        .replace(">", "&gt;").replace('"', "&quot;")


def generate_recovery_html(ws_map: dict, tabs: list, output: Path) -> None:
    """Generate an interactive HTML overview grouped by workspace/domain."""
    by_ws = defaultdict(list)
    for t in tabs:
        ws_id = t.get("workspaceId")
        ws_name = ws_map.get(ws_id) if ws_id else None
        key = ws_name or "Other / Workspace Unknown"
        by_ws[key].append(t)

    ws_order = sorted(by_ws.keys(), key=lambda k: -len(by_ws[k]))
    total = len(tabs)

    html = [f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Vivaldi Recovery</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0; display: flex; height: 100vh; overflow: hidden; }}
#sidebar {{ width: 240px; background: #0f0f1e; border-right: 1px solid #2a2a4e; overflow-y: auto; }}
#sidebar header {{ padding: 14px; border-bottom: 1px solid #2a2a4a; }}
#sidebar h1 {{ font-size: 13px; color: #7dc4e4; margin-bottom: 4px; }}
#sidebar p {{ font-size: 11px; color: #666; }}
.ws-btn {{ width: 100%; background: none; border: none; color: #9ab; padding: 10px 14px; cursor: pointer; text-align: left; border-left: 3px solid transparent; display: flex; justify-content: space-between; }}
.ws-btn:hover {{ background: #1a1a3e; }}
.ws-btn.active {{ background: #181838; border-left-color: #7dc4e4; color: #fff; font-weight: 600; }}
.ws-btn .cnt {{ font-size: 10px; color: #556; background: #1a2a4a; padding: 2px 6px; border-radius: 3px; }}
#main {{ flex: 1; display: flex; flex-direction: column; }}
#toolbar {{ padding: 10px 14px; background: #111; border-bottom: 1px solid #2a2a4a; }}
#search {{ background: #222; color: #eee; border: 1px solid #444; padding: 6px 10px; width: 100%; }}
#content {{ flex: 1; overflow-y: auto; padding: 14px 18px; }}
.ws-panel {{ display: none; }}
.ws-panel.active {{ display: block; }}
.domain-header {{ font-size: 11px; color: #668; padding: 6px 0; cursor: pointer; user-select: none; }}
.domain-header:hover {{ color: #9ac; }}
.url-list.hidden, .url-entry.hidden {{ display: none; }}
.url-entry {{ padding: 2px 0 2px 6px; }}
.url-entry a {{ color: #7dc4e4; text-decoration: none; font-size: 12px; }}
.url-entry a:hover {{ text-decoration: underline; }}
.url-entry .t {{ color: #889; font-size: 11px; margin-left: 6px; }}
</style></head><body>
<div id="sidebar"><header><h1>Vivaldi Recovery</h1><p>{total} tabs recovered</p></header>
"""]

    first_ws = None
    for ws_name in ws_order:
        if not by_ws[ws_name]:
            continue
        ws_id = re.sub(r"[^a-zA-Z0-9_]", "_", ws_name)
        if first_ws is None:
            first_ws = ws_id
        html.append(
            f'<button class="ws-btn" id="btn_{ws_id}" onclick="show(\'{ws_id}\')">'
            f'{esc(ws_name)} <span class="cnt">{len(by_ws[ws_name])}</span></button>\n'
        )

    html.append('</div><div id="main"><div id="toolbar">'
                '<input id="search" type="text" placeholder="Search..." '
                'oninput="search(this.value)"></div><div id="content">\n')

    for ws_name in ws_order:
        if not by_ws[ws_name]:
            continue
        ws_id = re.sub(r"[^a-zA-Z0-9_]", "_", ws_name)
        html.append(f'<div class="ws-panel" id="p_{ws_id}">\n')
        by_dom = defaultdict(list)
        for t in by_ws[ws_name]:
            try:
                dom = t["url"].split("/")[2]
            except (IndexError, KeyError):
                dom = "other"
            by_dom[dom].append(t)
        for domain, dtabs in sorted(by_dom.items(), key=lambda x: -len(x[1])):
            dom_id = re.sub(r"[^a-zA-Z0-9_]", "_", domain)
            html.append(f'<div class="domain-header" onclick="toggle(this)">'
                        f'{esc(domain)} ({len(dtabs)})</div>'
                        f'<div class="url-list" id="d_{ws_id}_{dom_id}">\n')
            for t in dtabs:
                title = esc(t.get("title", ""))
                html.append(
                    f'<div class="url-entry" data-url="{esc(t["url"])}">'
                    f'<a href="{esc(t["url"])}" target="_blank">{esc(t["url"][:80])}</a>'
                    f'<span class="t">{title}</span></div>\n'
                )
            html.append("</div>\n")
        html.append("</div>\n")

    html.append(f"""</div></div>
<script>
let current = null;
function show(id) {{
  if (current) {{
    document.getElementById('p_'+current).classList.remove('active');
    document.getElementById('btn_'+current).classList.remove('active');
  }}
  current = id;
  document.getElementById('p_'+id).classList.add('active');
  document.getElementById('btn_'+id).classList.add('active');
}}
function toggle(h) {{ h.nextElementSibling.classList.toggle('hidden'); }}
function search(q) {{
  q = q.toLowerCase();
  document.querySelectorAll('.url-entry').forEach(e =>
    e.classList.toggle('hidden', q && !e.getAttribute('data-url').includes(q)));
}}
show('{first_ws}');
</script></body></html>""")

    output.write_text("".join(html))


def build_report(profile_dir: Path | None, snapshot_name: str | None) -> Path:
    """Extract tabs from the newest (or named) snapshot and write the HTML.

    Skips rebuilding if the newest snapshot hasn't changed since the last
    report was generated (tracked via REPORT_FINGERPRINT_FILE).
    """
    sessions = find_sessions_dir(profile_dir)
    ws_map = load_workspaces(profile_dir or DEFAULT_VIVALDI_PROFILE)

    if snapshot_name:
        snap = SNAPSHOTS_DIR / snapshot_name
    else:
        snaps = sorted(d for d in SNAPSHOTS_DIR.iterdir() if d.is_dir())
        if not snaps:
            raise FileNotFoundError("no snapshots available — run a backup first")
        snap = snaps[-1]
        # Fingerprint gate: skip rebuild if snapshot content didn't change.
        try:
            last_fp = REPORT_FINGERPRINT_FILE.read_text().strip()
        except OSError:
            last_fp = ""
        snap_fp = ""
        fp_file = snap / ".fingerprint"
        if fp_file.exists():
            snap_fp = fp_file.read_text().strip()
        if last_fp and last_fp == snap_fp:
            return REPORT_FILE

    tabs = []
    for f in sorted(snap.rglob("*")):
        if f.is_file() and (f.suffix == GZIP_SUFFIX or f.suffix == ".bin"):
            tabs.extend(extract_tabs_from_snss(f))

    # Dedupe by URL, preferring entries with a workspace assignment.
    seen = {}
    for t in tabs:
        if t["url"] not in seen or (seen[t["url"]].get("workspaceId") is None
                                    and t.get("workspaceId")):
            seen[t["url"]] = t

    output = REPORT_FILE
    generate_recovery_html(ws_map, list(seen.values()), output)
    # Record the fingerprint for the next call.
    if snap_fp:
        REPORT_FINGERPRINT_FILE.write_text(snap_fp + "\n")
    return output


def cmd_report(args) -> int:
    try:
        out = build_report(args.profile_dir, args.snapshot)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"report: {out}")
    return 0


def cmd_backup(args) -> int:
    try:
        sessions = find_sessions_dir(args.profile_dir)
        status = snapshot(sessions, args.keep)
    except Exception as exc:  # noqa: BLE001
        write_status(last_error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(status, indent=2))
    return 0


def cmd_status(_args) -> int:
    print(json.dumps(load_status(), indent=2))
    return 0


def cmd_config(args) -> int:
    updates = {}
    if args.max_disk_mb is not None:
        updates["max_disk_mb"] = args.max_disk_mb
    if args.interval_min is not None:
        updates["interval_min"] = args.interval_min
    if updates:
        cfg = write_config(**updates)
        if "max_disk_mb" in updates:
            enforce_disk_budget(cfg.get("max_disk_mb"))
    else:
        cfg = load_config()
    print(json.dumps(cfg, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--profile-dir", type=Path, default=None,
                        help="Vivaldi profile dir (default: .../Vivaldi/Default)")
    parser.add_argument("--keep", type=int, default=48,
                        help="number of snapshots to retain (default 48)")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("backup", help="take a snapshot now").set_defaults(func=cmd_backup)
    sub.add_parser("status", help="print current status.json").set_defaults(func=cmd_status)
    cfg = sub.add_parser("config", help="show/set configuration")
    cfg.add_argument("--max-disk-mb", type=int, default=None,
                     help="max total disk for snapshots in MB (0 = unlimited)")
    cfg.add_argument("--interval-min", type=int, default=None,
                     help="backup interval in minutes (default 15)")
    cfg.set_defaults(func=cmd_config)
    rep = sub.add_parser("report", help="generate HTML recovery report")
    rep.add_argument("--snapshot", default=None,
                     help="snapshot name (default: newest)")
    rep.set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
