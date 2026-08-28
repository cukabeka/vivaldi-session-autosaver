#!/bin/bash
# Build the Chrome Web Store package: extension only, zipped.
# Store review requires the extension without native host files.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$REPO_DIR/dist"
OUT="$DIST/vivaldi-session-autosaver-store.zip"

rm -rf "$DIST"
mkdir -p "$DIST"
cp -R "$REPO_DIR/extension" "$DIST/extension"

# The store assigns its own extension ID; a repo-specific key is fine to
# keep for unpacked installs but is not required by the store. Keep it —
# it makes local testing and the store build share the same code.
(cd "$DIST/extension" && zip -qr "$OUT" .)

echo "Store package: $OUT"
echo "Upload at https://chrome.google.com/webstore/devconsole"
echo "After publishing, re-run: ./install.sh <store-extension-id>"
