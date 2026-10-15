# Auto AE for macOS

`build_macos_autoae.py` builds a self-contained app for the Mac architecture on which it
runs. It must run on macOS; it bundles the Python runtime and app dependencies. The universal
release ZIP is assembled for Intel and Apple Silicon by the GitHub Actions workflow
`.github/workflows/build-macos-release.yml`.

```
python3 -m pip install -r requirements-build-macos.txt -r macos/requirements-macos.txt
python3 build_macos_autoae.py
```

The native build makes `dist/Auto AE.app`. The release workflow merges Intel and Apple
Silicon builds and attaches `AutoAE-macos.zip` and `AutoAE-macos.dmg` to the specified
GitHub release. The ZIP is used for in-app updates; the DMG is the graphical installer for
new installations.

## Install the download

Open `AutoAE-macos.dmg`, drag Auto AE onto the Applications shortcut, eject the disk image,
and launch Auto AE from Applications. No Terminal commands or separate Python installation
are needed. The app and DMG are signed and notarized, and Gatekeeper stays enabled.

The ZIP is also available for manual installation: extract it in Finder, then drag `Auto AE.app`
to Applications.

## What the Mac needs

- **Python:** bundled. No separate installation is required.
- **FFmpeg** for Proxy Generator, File Converter and Media Browser: `brew install ffmpeg` (optional; requires
  Homebrew). Skip this if you do not have Homebrew; the rest of Auto AE works without FFmpeg.
  The app also looks in `/opt/homebrew/bin` and `/usr/local/bin`, or you can put `ffmpeg` and `ffprobe`
  in an `ffmpeg-mac` folder here before building.
- An internet connection is only needed for online features and update checks.

## First launch

Release downloads are signed with the project's Developer ID Application certificate and
notarized by Apple. If macOS still blocks the app, confirm that the download came from the
official Auto AE release, then follow the Auto AE-specific prompt in System Settings >
Privacy & Security. Never disable Gatekeeper globally.

## Where things live on the Mac

User settings and the updatable app source are in `~/Library/Application Support/AUTO_AE`
(in Finder: Go → Go to Folder…):

| | |
|---|---|
| `app/` | The copy of the code the app runs. Tools save their settings here too. |
| `venv/` | Used by older releases; the bundled-runtime app no longer needs it. |
| `autoae.log` | Output from the last run. Check here if a tool misbehaves. |
| `setup.log`, `launcher.log` | Setup details from older releases. |

Opening a newer build replaces the code in `app/` but keeps the settings your tools saved there,
and folder templates you edited are never overwritten.

## Checking for updates

Use **Check for Updates** in the app's bottom bar. Auto AE checks the latest stable release in
`CWrath/autoae` and asks before downloading or installing it. Windows opens the release
installer; macOS downloads the app ZIP, replaces the app bundle after it closes, then reopens
it. Your Application Support settings are kept. Users upgrading from an older release must
manually install v1.0.5 once; later updates can be installed in the app.

To publish an update, set `APP_VERSION` in `app_version.py`, build the Windows installer with
`build_windows_exe.py`, and create a GitHub release draft in `CWrath/autoae` with the matching
`vMAJOR.MINOR.PATCH` tag. Attach `AutoAE-Setup.exe`, then run the **Build universal macOS release**
workflow with that tag. The workflow builds on Intel and Apple Silicon runners, merges the native
binaries, signs and notarizes the app and DMG, staples the tickets, and uploads both macOS
assets. Configure these repository Actions secrets before running it:
`MACOS_CERTIFICATE_P12` (base64-encoded `.p12`), `MACOS_CERTIFICATE_PASSWORD`,
`MACOS_SIGNING_IDENTITY` (the full `Developer ID Application: Name (TEAMID)` identity),
`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID`. Attach both platform assets
before publishing. Keep the `.p12` and its password private; never commit them.

Build both platform assets from the same tagged source revision. The Windows installer bundles
all Python tools, and the macOS release workflow packages those same tools into the universal app.

## Files in this folder

| File | What it does |
|---|---|
| `frozen_entrypoint.py` | Starts the bundled Python runtime and app bootstrap. |
| `bootstrap.py` | Copies app source to Application Support, starts `main.py`, and explains startup errors. |
| `requirements-macos.txt` | Dependencies bundled into the Mac app. Optional packages enable their associated tools. |
| `Install Auto AE README.txt` | Finder-only installation steps for the DMG and ZIP. |
| `AppIcon.icns` | The app icon. |

The app uses its bundled Python runtime and does not depend on Python installed on the Mac.
