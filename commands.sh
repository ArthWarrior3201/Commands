#!/bin/sh
# Commands launcher for macOS and Linux. Needs Python 3.9 or newer.
here="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
if command -v python3 >/dev/null 2>&1; then exec python3 "$here/run.py" "$@"; fi
exec python "$here/run.py" "$@"
