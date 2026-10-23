#!/usr/bin/env python3
"""Build the Windows Auto AE distribution in dist-windows/Auto AE."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from app_version import APP_VERSION

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"
FFMPEG_DIR = ROOT / "ffmpeg-7.1.1-essentials_build" / "bin"
ICON = ROOT / "assets" / "autoae.ico"
DIST_DIR = ROOT / "dist-windows-installer"
INSTALLER_SCRIPT = ROOT / "windows" / "AutoAE.iss"


def find_inno_setup_compiler():
    compiler = shutil.which("ISCC.exe")
    if compiler:
        return Path(compiler)

    install_dirs = (
        Path(r"C:\Program Files (x86)") / "Inno Setup 6",
        Path(r"C:\Program Files") / "Inno Setup 6",
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Programs" / "Inno Setup 6",
    )
    for install_dir in install_dirs:
        candidate = install_dir / "ISCC.exe"
        if candidate.is_file():
            return candidate
    return None


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable on Windows.")

    compiler = find_inno_setup_compiler()
    if compiler is None:
        raise SystemExit(
            "Inno Setup 6 is required to create the single-file installer. "
            "Install it from https://jrsoftware.org/isdl.php and run this build again."
        )

    missing = [path for path in (TOOLS_DIR, FFMPEG_DIR, ICON, INSTALLER_SCRIPT) if not path.exists()]
    if missing:
        raise SystemExit("Required build files are missing: " + ", ".join(map(str, missing)))

    ffmpeg_files = [FFMPEG_DIR / "ffmpeg.exe", FFMPEG_DIR / "ffprobe.exe"]
    missing_ffmpeg = [path for path in ffmpeg_files if not path.is_file()]
    if missing_ffmpeg:
        raise SystemExit("Required FFmpeg files are missing: " + ", ".join(map(str, missing_ffmpeg)))

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name", "Auto AE",
        "--icon", str(ICON),
        "--distpath", str(DIST_DIR),
        "--workpath", str(ROOT / "build-windows"),
        "--specpath", str(ROOT / "build-windows"),
        "--collect-all", "customtkinter",
        "--collect-all", "tkinterdnd2",
        "--collect-all", "pymupdf",
        "--add-data", f"{ROOT / 'assets'};assets",
        "--add-data", f"{ROOT / 'autoae_theme.json'};.",
        "--add-data", f"{ROOT / 'foldertemplates'};foldertemplates",
    ]

    for tool_file in sorted(TOOLS_DIR.glob("*.py")):
        if tool_file.name != "__init__.py":
            command.extend(("--hidden-import", f"tools.{tool_file.stem}"))
            command.extend(("--add-data", f"{tool_file};tools"))

    for ffmpeg_file in ffmpeg_files:
        command.extend(("--add-data", f"{ffmpeg_file};ffmpeg-7.1.1-essentials_build/bin"))

    command.append(str(ROOT / "main.py"))
    subprocess.run(command, cwd=ROOT, check=True)

    subprocess.run(
        [str(compiler), "/Qp", "/DAppVersion={}".format(APP_VERSION), str(INSTALLER_SCRIPT)],
        cwd=ROOT,
        check=True,
    )

    installer = DIST_DIR / "AutoAE-Setup.exe"
    print(f"\nInstaller complete: {installer}")
    print("Share this single installer file; recipients do not need Python installed.")


if __name__ == "__main__":
    main()
