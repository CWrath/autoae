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
