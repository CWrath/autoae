# Auto AE for Windows

## Build

Install [Inno Setup 6](https://jrsoftware.org/isdl.php), then build on Windows
using Python 3.10 or newer:

```powershell
python -m pip install -r requirements-build-windows.txt
python build_windows_exe.py
```

The distributable installer is `dist-windows-installer/AutoAE-Setup.exe`. Share
that single file. It installs Auto AE under the current user's Local AppData
without requiring administrator privileges, adds a Start Menu shortcut, offers
an optional desktop shortcut, and can launch the app when setup finishes. Python
is not needed on the target computer.

The installer packages the PyInstaller one-folder app, including its tools,
resources, and bundled FFmpeg/FFprobe. Build outputs go to `build-windows/` and
`dist-windows-installer/`, leaving other builds and the macOS release in `dist/`
alone.
