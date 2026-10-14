AUTO AE FOR macOS — INSTALLATION
================================

This package is not signed or notarized by Apple. These steps remove the download
quarantine flag from Auto AE only; they do not disable Gatekeeper or change macOS
security settings for other apps.

REQUIREMENTS
------------

You do not need to install Python or Homebrew. Auto AE includes its own Python
runtime, Tk, and the Python packages used by its tools. An internet connection
is not needed to set up Python dependencies on first launch.

INSTALL
-------

1. Extract AutoAE-macos.zip in Finder.
2. Open Terminal. Type "cd " (including the trailing space), drag the extracted
   folder from Finder into Terminal, then press Return. This sets the current
   directory to the folder containing "Auto AE.app".
3. Copy and paste the commands below into Terminal, then press Return:

    APP_SOURCE="$PWD/Auto AE.app"
    APP_TARGET="$HOME/Applications/Auto AE.app"

    if [ ! -d "$APP_SOURCE" ]; then
      echo 'Auto AE.app was not found. Run these commands from the extracted folder.' >&2
      exit 1
    fi

    mkdir -p "$HOME/Applications"
    /usr/bin/ditto "$APP_SOURCE" "$APP_TARGET"

    if /usr/bin/xattr -p com.apple.quarantine "$APP_TARGET" >/dev/null 2>&1; then
      /usr/bin/xattr -dr com.apple.quarantine "$APP_TARGET"
    fi

    /usr/bin/open "$APP_TARGET"

FIRST LAUNCH
------------

Auto AE starts directly with its bundled Python runtime and dependencies.

If macOS still blocks the app, open System Settings > Privacy & Security, select
Open Anyway for Auto AE, and confirm. Never disable Gatekeeper globally.

Auto AE is installed at:

    ~/Applications/Auto AE.app

FFmpeg is optional and is needed only by some media tools. If you already have
Homebrew, you can install it with:

    brew install ffmpeg

If you do not have Homebrew, skip this optional step. The rest of Auto AE works
without FFmpeg.
