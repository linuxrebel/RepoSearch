#!/bin/bash
# Thin wrapper — delegates to the Python launcher for cross-platform support.
exec python3 "$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0")")" && pwd)/repo-browser.py" "$@"
