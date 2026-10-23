# Auto AE

Auto AE is a desktop toolkit for assistant editors, built with Python and
CustomTkinter. It includes media, editorial, storage, and project-setup tools.

## Features

- **Media:** Browse and preview media, convert common image/audio/video formats,
  generate proxies, consolidate media with cross-platform-safe destination names,
  and export PDF pages as PNG or JPEG.
- **Editorial:** Convert EDL/ALE/XML files to spreadsheets, turn subtitles into
  timeline markers, calculate timecodes, and sync Avid bins.
- **Project setup:** Create project folder structures, organize watch folders,
  automatically rename and route incoming media with saved watch rules, back up
  projects, and carry editing shortcuts, workspaces, presets, and plug-ins between
  machines.
- **Storage & utilities:** Compare drives, monitor disk space, check USB cable
  data capability, and batch-rename files.
- **Workflow:** Track tasks from email and Slack, and check for app updates from
  within the launcher.

## Run from source

Install Python 3.10 or newer, then install the Windows dependencies:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Some media tools use FFmpeg. Windows packages include it; on macOS, use the
**Install FFmpeg** button in Auto AE when prompted. The optional download needs
internet access and installs only inside Auto AE's user support folder.

## Build releases

- Windows: run `python build_windows_exe.py` on Windows with PyInstaller and
  Inno Setup 6 installed.
- macOS: Python and its dependencies are bundled. Create a GitHub release draft, then run
  the **Build universal macOS release** workflow to build for Intel and Apple Silicon and
  attach `AutoAE-macos.zip` (in-app updates), `AutoAE-macos.dmg` (drag-to-Applications
  installer), and the signed universal FFmpeg download plus its corresponding source
  archive. Build all platform assets from the same version tag before publishing.

The in-app updater checks the latest stable GitHub release. Update releases
must use a `vMAJOR.MINOR.PATCH` tag and include `AutoAE-Setup.exe`,
`AutoAE-macos.zip`, `AutoAE-macos.dmg`, `AutoAE-ffmpeg-macos.zip`, and the
matching `AutoAE-ffmpeg-source-9.0.2.tar.xz` source archive.
