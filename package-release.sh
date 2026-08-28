#!/bin/bash
# Build a self-contained release zip: extension + helper + installer + docs.
# Unlike package-store.sh (extension only, for the Web Store), this zip is
# meant for direct distribution — a user can unzip it and run ./install.sh.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$REPO_DIR/dist"
NAME="vivaldi-session-autosaver"
OUT="$DIST/$NAME.zip"
STAGE="$DIST/$NAME"

rm -rf "$STAGE"
mkdir -p "$STAGE"

# Everything a user needs to install from source
cp -R "$REPO_DIR/extension" "$STAGE/extension"
cp -R "$REPO_DIR/helper" "$STAGE/helper"
find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} +
cp "$REPO_DIR/install.sh" "$REPO_DIR/uninstall.sh" "$STAGE/"
cp "$REPO_DIR/README.md" "$REPO_DIR/LICENSE" "$STAGE/"
chmod +x "$STAGE/install.sh" "$STAGE/uninstall.sh"

# Short getting-started note so the zip is self-explanatory without GitHub
cat > "$STAGE/QUICKSTART.txt" <<'EOF'
Vivaldi Session Autosaver — Quick Start
=======================================

Automatic point-in-time snapshots of your Vivaldi sessions and workspaces,
protecting against workspace tabs disappearing after closing a window.

Requirements
------------
- macOS
- Python 3 (any recent version, stdlib only — no pip packages needed)
- Vivaldi

Install
-------
1. Open a terminal in this folder.
2. Run:

       ./install.sh

   This installs the Python helper to ~/.vivaldi-session-autosaver/bin/,
   registers it with Vivaldi via Native Messaging, and runs the first
   backup.

3. Load the extension:
   - Open vivaldi://extensions
   - Enable "Developer mode"
   - Click "Load unpacked" and select the "extension" folder from this zip
   - The extension connects automatically and shows backup status.

Verify
------
- Snapshots land in ~/.vivaldi-session-autosaver/snapshots/
- Open ~/.vivaldi-session-autosaver/recovery_report.html to see every
  saved tab, grouped by workspace.

Uninstall
---------
    ./uninstall.sh

More details (CLI usage, retention policy, limitations): see README.md
EOF

rm -f "$OUT"
(cd "$DIST" && zip -qr "$OUT" "$NAME")
rm -rf "$STAGE"

echo "Release package: $OUT"
echo "Contents: extension/ helper/ install.sh uninstall.sh README.md LICENSE QUICKSTART.txt"
