# Auto AE

Auto AE is a desktop toolkit for assistant editors, built with Python and CustomTkinter. It includes media, editorial, storage, and project-setup tools.

## Features

- **Media:** Browse and preview media, convert common image/audio/video formats, generate proxies, and export PDF pages as PNG or JPEG.
- **Editorial:** Convert EDL/ALE/XML files to spreadsheets, turn subtitles into timeline markers, calculate timecodes, and sync Avid bins.
- **Project setup:** Create project folder structures, organize watch folders, back up projects, and carry editing shortcuts, workspaces, presets, and plug-ins between machines.
- **Storage & utilities:** Compare drives, monitor disk space, check USB cable data capability, and batch-rename files.
- **Workflow:** Track tasks from email and Slack, and check for app updates from within the launcher.

## Run from source

Install Python 3.10 or newer, then install the Windows dependencies:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Some tools also use FFmpeg. Install it separately or use the bundled Windows build when packaging.

## Build releases

- Windows: run `python build_windows_exe.py` on Windows with PyInstaller and Inno Setup 6 installed.
- macOS: run `python build_macos_autoae.py`; the generated app uses the Mac's Python and installs its requirements on first launch.

The in-app updater checks the latest stable GitHub release. Update releases must use a `vMAJOR.MINOR.PATCH` tag and include `AutoAE-Setup.exe` and `AutoAE-macos.zip`.
# Auto AE

Auto AE is a desktop toolkit for assistant editors, built with Python and CustomTkinter. It includes media, editorial, storage, and project-setup tools.

## Run from source

Install Python 3.10 or newer, then install the Windows dependencies:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Some tools also use FFmpeg. Install it separately or use the bundled Windows build when packaging.

## Build releases

- Windows: run `python build_windows_exe.py` on Windows with PyInstaller and Inno Setup 6 installed.
- macOS: run `python build_macos_autoae.py`; the generated app uses the Mac's Python and installs its requirements on first launch.

The in-app updater checks the latest stable GitHub release. Update releases must use a `vMAJOR.MINOR.PATCH` tag and include `AutoAE-Setup.exe` and `AutoAE-macos.zip`.
