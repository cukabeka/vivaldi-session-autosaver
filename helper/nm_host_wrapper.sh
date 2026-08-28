#!/bin/bash
# Native Messaging host wrapper.
# Chromium NM manifests do not support an "args" field, so the browser
# launches this script directly; it execs the real Python host.
exec "@PY_BIN@" "@HOME@/.vivaldi-session-autosaver/bin/nm_host.py"
