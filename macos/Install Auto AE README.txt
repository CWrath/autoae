AUTO AE FOR macOS — INSTALLATION
================================

Official Auto AE releases are signed with the project's Developer ID certificate
and notarized by Apple. These installation steps preserve macOS Gatekeeper checks.

REQUIREMENTS
------------

You do not need to install Python or Homebrew. Auto AE includes its own Python
runtime, Tk, and the Python packages used by its tools. An internet connection
is not needed to set up Python dependencies on first launch.

INSTALL
-------

1. Open AutoAE-macos.dmg (skip this step if you are reading this from the open disk image).
2. Drag Auto AE onto the Applications shortcut in the window.
3. Eject the Auto AE disk image, then open Auto AE from Applications.

No Terminal commands, Python, or Homebrew are required to install or run Auto AE.

FIRST LAUNCH
------------

Auto AE starts directly with its bundled Python runtime and dependencies. An
internet connection is needed for the first update check, online features, and
the optional FFmpeg download.

If macOS blocks the app, first confirm that it came from the official Auto AE
release. Then open System Settings > Privacy & Security and follow the prompt
for Auto AE. Never disable Gatekeeper globally.

Auto AE is installed at:

    ~/Applications/Auto AE.app

FFmpeg is optional and is needed only by some media tools. If it is missing,
click Install beside the FFmpeg status in Auto AE. The app downloads the signed
FFmpeg and FFprobe tools and keeps them in its Application Support folder. This
requires an internet connection; no Terminal, Homebrew, or administrator access
is needed. The rest of Auto AE works without FFmpeg.
