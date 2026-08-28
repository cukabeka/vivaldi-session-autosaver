#!/bin/bash
# Vivaldi Session Autosaver — uninstaller (macOS)
set -euo pipefail

APP_DIR="$HOME/.vivaldi-session-autosaver"
NM_DIR="$HOME/Library/Application Support/Vivaldi/NativeMessagingHosts"
NM_NAME="com.cukabeka.vivaldi_session_autosaver"

rm -f "$NM_DIR/$NM_NAME.json"
rm -rf "$APP_DIR"

echo "Uninstalled. Snapshots were removed with $APP_DIR."
