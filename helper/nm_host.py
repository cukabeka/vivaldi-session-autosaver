#!/usr/bin/env python3
"""Vivaldi Session Autosaver — Native Messaging host.

Reads length-framed JSON messages from stdin (Native Messaging protocol),
executes backup commands, and replies with status JSON. Long-running;
launched by Vivaldi when the extension opens a port.

Protocol:
  in:  {"type": "status"} | {"type": "backup_now"}
  out: {"type": "status", "status": {...}} | {"type": "error", "message": "..."}
"""

from __future__ import annotations

import json
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vivaldi_session_autosaver as core  # noqa: E402


def _interval_min() -> int:
    return core.load_config().get("interval_min", core.DEFAULT_INTERVAL_MIN)


def read_message() -> dict | None:
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    (length,) = struct.unpack("<I", raw)
    if length == 0 or length > 1_000_000:
        return None
    return json.loads(sys.stdin.buffer.read(length))


def send_message(msg: dict) -> None:
    data = json.dumps(msg).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def handle(msg: dict) -> dict:
    mtype = msg.get("type")
    if mtype == "status":
        status = core.load_status()
        status.setdefault("interval_min", _interval_min())
        status.setdefault("snapshots", core.list_snapshots())
        status.setdefault("report_path", str(core.REPORT_FILE))
        status.setdefault("config", core.load_config())
        return {"type": "status", "status": status}
    if mtype == "set_config":
        updates = msg.get("config", {})
        allowed = {k: v for k, v in updates.items() if k in ("max_disk_mb", "interval_min")}
        cfg = core.write_config(**allowed)
        freed = core.enforce_disk_budget(cfg.get("max_disk_mb"))
        status = core.load_status()
        status["config"] = cfg
        status["snapshots"] = core.list_snapshots()
        status["freed_bytes"] = freed
        return {"type": "status", "status": status}
    if mtype == "backup_now":
        try:
            sessions = core.find_sessions_dir(None)
            status = core.snapshot(sessions, keep=48)
            status.setdefault("interval_min", _interval_min())
            status.setdefault("report_path", str(core.REPORT_FILE))
            status.setdefault("config", core.load_config())
            try:
                core.build_report(None, None)
            except Exception:  # report is best-effort
                pass
            return {"type": "status", "status": status}
        except Exception as exc:  # noqa: BLE001
            return {"type": "error", "message": str(exc)}
    return {"type": "error", "message": f"unknown message type: {mtype!r}"}


def periodic_backup() -> None:
    """Snapshot on start, then every interval_min minutes.

    Re-reads the interval from config each loop iteration so it picks up
    changes made via set_config or CLI without restarting Vivaldi.
    Runs in a daemon thread; the process only lives as long as Vivaldi
    keeps the native messaging port open.
    """
    while True:
        try:
            sessions = core.find_sessions_dir(None)
            core.snapshot(sessions, keep=48)
            try:
                core.build_report(None, None)
            except Exception:  # report is best-effort
                pass
        except Exception as exc:  # noqa: BLE001
            try:
                core.write_status(last_error=str(exc), interval_min=_interval_min())
            except Exception:
                pass
        interval = _interval_min()
        if interval < 1:
            interval = core.DEFAULT_INTERVAL_MIN
        time.sleep(interval * 60)


def main() -> int:
    threading.Thread(target=periodic_backup, daemon=True).start()
    while True:
        try:
            msg = read_message()
        except (json.JSONDecodeError, OSError):
            return 1
        if msg is None:
            return 0  # port closed → Vivaldi gone → process exits
        send_message(handle(msg))


if __name__ == "__main__":
    sys.exit(main())
