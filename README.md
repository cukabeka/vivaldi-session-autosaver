# Vivaldi Session Autosaver

Automatic point-in-time snapshots of your Vivaldi sessions and workspaces —
protecting against the known macOS failure mode where workspace tab
assignments disappear after closing a window
([forum topic 114232](https://forum.vivaldi.net/topic/114232/bug-session-tabs-and-workspaces-empty-after-closing-window)
and related reports).

Two components:

1. **Helper** (Python 3, stdlib only) — snapshots Vivaldi's profile
   `Sessions/` directory (SNSS files) into
   `~/.vivaldi-session-autosaver/snapshots/<timestamp>/`. The helper is
   spawned by Vivaldi itself via Native Messaging — **no background daemon**.
   It snapshots on connect and every 15 minutes while Vivaldi runs, applies
   **GFS retention** (hourly ×24, daily ×7, weekly ×4), gzips all files
   (~87 % smaller), and generates an HTML recovery report of all tabs and
   workspaces.
2. **Extension** (MV3) — Safari-style status dashboard: backup health badge,
   snapshot list with retention buckets, "Back Up Now" button, and a
   copy/paste install command when the helper isn't connected yet.

Communication is via Chrome Native Messaging. Snapshots are excluded from
Vivaldi's own crash backups and user archives, so a typical snapshot is
~15 MB instead of ~300 MB.

## Install

```bash
git clone https://github.com/cukabeka/vivaldi-session-autosaver
cd vivaldi-session-autosaver
./install.sh
```

The installer:

1. Generates a stable extension key in `extension/manifest.json` (idempotent)
   and derives the extension ID from it (Chromium algorithm).
2. Selects the best available Python 3 and installs the helper to
   `~/.vivaldi-session-autosaver/bin/`.
3. Registers the Native Messaging manifest in
   `~/Library/Application Support/Vivaldi/NativeMessagingHosts/`.
4. Runs the first backup.

Then load the extension: `vivaldi://extensions` → Developer mode →
**Load unpacked** → select `extension/`. It connects automatically.

## Uninstall

```bash
./uninstall.sh
```

## CLI

```bash
~/.vivaldi-session-autosaver/bin/vivaldi_session_autosaver.py backup   # snapshot now
~/.vivaldi-session-autosaver/bin/vivaldi_session_autosaver.py status   # print status.json
~/.vivaldi-session-autosaver/bin/vivaldi_session_autosaver.py report   # regenerate HTML report
```

Flags: `--profile-dir <path>` for non-default profiles, `--keep N` as a flat
retention upper bound (GFS buckets decide what survives).

The recovery report (`~/.vivaldi-session-autosaver/recovery_report.html`)
lists every tab from the newest snapshot grouped by workspace and domain —
your safety net if Vivaldi ever empties a workspace.

## How retention works (GFS)

| Bucket | Keeps |
|---|---|
| Hourly | newest snapshot of each of the last 24 hours |
| Daily | newest snapshot of each of the last 7 days |
| Weekly | newest snapshot of each of the last 4 weeks |

The newest snapshot is always kept; anything older than 5 weeks is removed.
Snapshots whose content hasn't changed are skipped (fingerprint check).

## Limitations (MVP)

- **Backup only** — restore is not implemented yet. To restore manually:
  quit Vivaldi, replace the profile `Sessions/` directory with a snapshot's
  contents (gunzip the `.gz` files), restart Vivaldi.
- Snapshots are taken while Vivaldi may be running; SNSS files are
  append-mostly so copies are usually consistent, but a snapshot taken
  mid-write can be incomplete.
- Default profile only unless `--profile-dir` is given.
- macOS only.

## Chrome Web Store / packaging

Run `./package-store.sh` to build `dist/vivaldi-session-autosaver-store.zip`
containing only the extension (what the Web Store expects). The store build
uses a store-managed extension ID, so after publishing you must re-run
`./install.sh <store-extension-id>` to register the native host for it.

## Roadmap

- Restore command (helper stops Vivaldi, swaps snapshot, restarts).
- Multi-profile detection via `Local State`.
- APFS snapshot integration for crash-consistent copies.

## License

MIT — see [LICENSE](LICENSE).
