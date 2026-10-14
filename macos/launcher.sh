#!/bin/bash
# Auto AE for macOS. This becomes "Auto AE.app/Contents/MacOS/AutoAE".
# It finds a Python 3 with Tk on this Mac and hands over to bootstrap.py, which
# installs what the app needs on first launch and then starts it.

CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
RESOURCES="$CONTENTS/Resources"
SUPPORT="${AUTOAE_SUPPORT_DIR:-$HOME/Library/Application Support/AUTO_AE}"
export AUTOAE_APP_BUNDLE="$(cd "$CONTENTS/.." && pwd)"
LOG="$SUPPORT/launcher.log"

# Apps opened from Finder only get /usr/bin:/bin:/usr/sbin:/sbin, so add Homebrew and MacPorts (for ffmpeg).
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:$PATH"
export TK_SILENCE_DEPRECATION=1
unset PYTHONHOME PYTHONPATH

mkdir -p "$SUPPORT"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ]; then
    mv -f "$LOG" "$LOG.old"
fi
echo "---- $(date '+%Y-%m-%d %H:%M:%S') opening $CONTENTS" >> "$LOG"

# Exit 0: Python 3.10+ with a working tkinter. Exit 2: Tk 9 on a Python from before the
# tkinter fix for Tk 9 (3.13.11 / 3.14.1); tools that work in the background would crash.
CHECK='
import sys, tkinter
v = sys.version_info
if v < (3, 10):
    sys.exit(1)
if tkinter.TkVersion >= 9 and not (v >= (3, 14, 1) or (3, 13, 11) <= v < (3, 14)):
    sys.exit(2)
'
PYTHON=""
OUTDATED=""

try_python() {
    [ -z "$PYTHON" ] || return 0
    [ -n "$1" ] && [ -x "$1" ] || return 1
    "$1" -c "$CHECK" >/dev/null 2>&1
    case $? in
        0) PYTHON="$1" ;;
        2) [ -n "$OUTDATED" ] || OUTDATED="$1" ;;
    esac
    [ -n "$PYTHON" ]
}

if [ -n "$AUTOAE_PYTHON" ]; then
    try_python "$AUTOAE_PYTHON"
fi
# Keep the Python Auto AE was set up with, so installing another one doesn't trigger a new setup.
if [ -z "$PYTHON" ] && [ -f "$SUPPORT/python-path.txt" ]; then
    try_python "$(cat "$SUPPORT/python-path.txt")"
fi
if [ -z "$PYTHON" ]; then
    for version in 3.13 3.12 3.11 3.14 3.10; do
        for candidate in \
            "/Library/Frameworks/Python.framework/Versions/$version/bin/python$version" \
            "/opt/homebrew/opt/python@$version/bin/python$version" \
            "/usr/local/opt/python@$version/bin/python$version"; do
            try_python "$candidate" && break 2
        done
    done
fi
if [ -z "$PYTHON" ]; then
    for name in python3.13 python3.12 python3.11 python3.14 python3.10 python3; do
        try_python "$(command -v "$name" 2>/dev/null)" && break
    done
fi

if [ -z "$PYTHON" ] && [ -n "$OUTDATED" ]; then
    echo "only an outdated Python with Tk 9 found: $OUTDATED" >> "$LOG"
    osascript -e 'display dialog "Auto AE needs a newer Python.\n\nThe Python it found uses Tk 9, which needs Python 3.13.11 or newer. To update Homebrew'"'"'s Python, run this in Terminal:\n\n    brew upgrade python@3.13 python-tk@3.13\n\nThen open Auto AE again." with title "Auto AE" buttons {"OK"} default button "OK" with icon caution' >/dev/null 2>&1
    exit 1
fi
if [ -z "$PYTHON" ]; then
    echo "no Python 3.10+ with tkinter found" >> "$LOG"
    choice="$(osascript -e 'button returned of (display dialog "Auto AE needs Python 3.10 or newer with Tk.\n\nInstall Python from python.org (it includes Tk), or with Homebrew run:\n\n    brew install python@3.13 python-tk@3.13\n\nThen open Auto AE again." with title "Auto AE" buttons {"Cancel", "Get Python"} default button "Get Python" with icon caution)' 2>/dev/null)"
    if [ "$choice" = "Get Python" ]; then
        open "https://www.python.org/downloads/macos/"
    fi
    exit 1
fi

echo "python: $PYTHON" >> "$LOG"
exec "$PYTHON" "$RESOURCES/bootstrap.py" "$@"
