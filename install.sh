#!/bin/bash
# Vivaldi Session Autosaver — installer (macOS)
# Usage: ./install.sh [extension-id]
# Fully self-contained: derives everything from the environment.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.vivaldi-session-autosaver"
BIN_DIR="$APP_DIR/bin"
NM_DIR="$HOME/Library/Application Support/Vivaldi/NativeMessagingHosts"
LABEL="com.cukabeka.vivaldi-session-autosaver"
NM_NAME="com.cukabeka.vivaldi_session_autosaver"
MANIFEST="$REPO_DIR/extension/manifest.json"

# Optional override; otherwise derived from the generated key below.
EXT_ID="${1:-${EXTENSION_ID:-}}"
EXT_ID="${EXT_ID#chrome-extension://}"; EXT_ID="${EXT_ID%/}"

echo "==> Ensuring stable extension key in $MANIFEST"
if grep -q "REPLACE_WITH_GENERATED_KEY" "$MANIFEST"; then
  KEY_B64="$(openssl genrsa 2048 2>/dev/null | openssl rsa -pubout -outform DER 2>/dev/null | openssl base64 -A)"
  python3 - "$MANIFEST" "$KEY_B64" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
data = json.loads(open(path).read())
data["key"] = key
open(path, "w").write(json.dumps(data, indent=2) + "\n")
PY
  echo "    key generated and written."
else
  echo "    key already present, keeping it."
fi

# Derive the extension ID from the manifest key (Chromium algorithm:
# SHA-256 of the DER public key, first 16 bytes as hex digits 0-f -> a-p).
if [[ -z "$EXT_ID" ]]; then
  EXT_ID="$(python3 - "$MANIFEST" <<'PY'
import base64, hashlib, json, sys
key = json.loads(open(sys.argv[1]).read())["key"]
der = base64.b64decode(key)
digest = hashlib.sha256(der).hexdigest()[:32]
print("".join(chr(97 + int(c, 16)) for c in digest))
PY
)"
  echo "==> Derived extension ID: $EXT_ID"
fi

echo "==> Selecting Python interpreter"
# Prefer a modern Homebrew Python; fall back to /usr/bin/python3 (3.9 works
# because the helper uses `from __future__ import annotations`).
PY_BIN=""
for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [[ -x "$cand" ]]; then PY_BIN="$cand"; break; fi
done
echo "    using $PY_BIN"

echo "==> Installing helper to $BIN_DIR"
mkdir -p "$BIN_DIR" "$APP_DIR/snapshots"

cp "$REPO_DIR/helper/vivaldi_session_autosaver.py" "$BIN_DIR/"
cp "$REPO_DIR/helper/nm_host.py" "$BIN_DIR/"
chmod +x "$BIN_DIR"/*.py

# Chromium NM manifests do not support "args", so the browser launches a
# wrapper script that execs the real Python host.
sed -e "s|@PY_BIN@|$PY_BIN|" -e "s|@HOME@|$HOME|" \
    "$REPO_DIR/helper/nm_host_wrapper.sh" > "$BIN_DIR/nm_host_wrapper.sh"
chmod +x "$BIN_DIR/nm_host_wrapper.sh"

echo "==> Installing Native Messaging manifest"
mkdir -p "$NM_DIR"
sed -e "s|@HOME@|$HOME|" \
    -e "s|REPLACE_EXTENSION_ID|$EXT_ID|" \
    "$REPO_DIR/helper/$NM_NAME.json" \
    > "$NM_DIR/$NM_NAME.json"

echo "==> Running first backup"
"$PY_BIN" "$BIN_DIR/vivaldi_session_autosaver.py" backup

echo
echo "Done. Next steps:"
echo "  1. Open vivaldi://extensions → Load unpacked → select: $REPO_DIR/extension"
echo "  2. The extension will connect automatically (ID: $EXT_ID)."
echo "     Backups then run while Vivaldi is running (no background daemon)."
echo "Status: cat $APP_DIR/status.json"
