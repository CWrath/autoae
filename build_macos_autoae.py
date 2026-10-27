#!/usr/bin/env python3
"""Build one native-architecture, self-contained macOS Auto AE app.

Run this on a Mac after installing the dependencies in macos/requirements-macos.txt
and PyInstaller. The universal release is assembled from separate Intel and Apple
Silicon builds by macos/merge_universal_app.py.
"""

import hashlib
import json
import platform
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app_version import APP_VERSION

ROOT = Path(__file__).resolve().parent
MAC = ROOT / "macos"
DIST = ROOT / "dist"
WORK = ROOT / "build-macos-frozen"
APP_NAME = "Auto AE"
EXECUTABLE = "Auto AE"
BUNDLE_ID = "com.autoae.app"

APP_FILES = [
    "main.py", "theme.py", "base_tool.py", "tool_loader.py", "tool_template.py",
    "app_updater.py", "app_version.py", "ffmpeg_setup.py", "ffmpeg_installer.py",
    "error_help.py", "drag_drop.py", "edl_report_core.py", "music_cue_core.py", "cloud_storage.py", "background_services.py", "tray_agent.py",
    "taskboard_core.py", "autoae_theme.json", "UI_THEME_GUIDE.md",
]
APP_GLOBS = [
    ("tools", "*.py"), ("assets", "**/*"), ("foldertemplates", "*.csv"),
    ("ffmpeg-mac", "*"),
]
SEED_DIRS = ("foldertemplates",)
EXECUTABLE_DIRS = ("ffmpeg-mac",)
COLLECT_PACKAGES = (
    "certifi", "customtkinter", "darkdetect", "PIL", "numpy", "pandas", "openpyxl", "docx", "lxml",
    "matplotlib", "cv2", "psutil", "schedule", "chardet", "watchdog", "tkinterdnd2",
    "pymediainfo", "pygame", "keyring", "pymupdf", "fitz",
)


def collect_app_files():
    files = [name for name in APP_FILES if (ROOT / name).is_file()]
    missing = [name for name in APP_FILES if name not in files]
    if missing:
        raise SystemExit("Missing from {}: {}".format(ROOT, ", ".join(missing)))
    for folder, pattern in APP_GLOBS:
        base = ROOT / folder
        if not base.is_dir():
            continue
        for path in sorted(base.glob(pattern)):
            relative = path.relative_to(ROOT)
            if path.is_file() and "__pycache__" not in relative.parts:
                if not any(part.startswith(".") for part in relative.parts):
                    files.append(relative.as_posix())
    return files


def prepare_bundle_resources(resources):
    app_dir = resources / "app"
    app_dir.mkdir(parents=True)
    manifest = {"files": {}, "seed": [], "executable": []}
    for relative in collect_app_files():
        source = ROOT / relative
        target = app_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest["files"][relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        if relative.split("/")[0] in SEED_DIRS:
            manifest["seed"].append(relative)
        if relative.split("/")[0] in EXECUTABLE_DIRS:
            manifest["executable"].append(relative)

    manifest["build"] = hashlib.sha256(
        json.dumps(manifest["files"], sort_keys=True).encode()
    ).hexdigest()[:16]
    manifest["version"] = APP_VERSION
    manifest["built"] = datetime.now().isoformat(timespec="seconds")
    (resources / "app-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    shutil.copyfile(MAC / "bootstrap.py", resources / "bootstrap.py")
    shutil.copyfile(MAC / "requirements-macos.txt", resources / "requirements-macos.txt")
    return manifest


def build_app():
    if sys.platform != "darwin":
        raise SystemExit("Build the self-contained macOS app on macOS.")
    if platform.machine() not in ("arm64", "x86_64"):
        raise SystemExit("Auto AE supports Apple Silicon and Intel Macs.")
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise SystemExit("Install PyInstaller with: python3 -m pip install PyInstaller") from exc

    if WORK.exists():
        shutil.rmtree(WORK)
    bundle_resources = WORK / "bundle"
    manifest = prepare_bundle_resources(bundle_resources)
    DIST.mkdir(exist_ok=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", APP_NAME,
        "--icon", str(MAC / "AppIcon.icns"),
        "--distpath", str(DIST),
        "--workpath", str(WORK / "pyinstaller"),
        "--specpath", str(WORK),
        "--target-architecture", platform.machine(),
        "--osx-bundle-identifier", BUNDLE_ID,
        "--hidden-import", "tkinter",
        "--hidden-import", "_tkinter",
        "--hidden-import", "filecmp",
        "--hidden-import", "imaplib",
        "--add-data", "{}:bundle".format(bundle_resources),
    ]
    for package in COLLECT_PACKAGES:
        command.extend(("--collect-all", package))
    command.append(str(MAC / "frozen_entrypoint.py"))
    subprocess.run(command, cwd=ROOT, check=True)

    app_dir = DIST / "{}.app".format(APP_NAME)
    info_path = app_dir / "Contents" / "Info.plist"
    with open(info_path, "rb") as handle:
        info = plistlib.load(handle)
    info.update({
        "CFBundleDisplayName": APP_NAME,
        "CFBundleName": APP_NAME,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "LSMinimumSystemVersion": "11.0",
    })
    with open(info_path, "wb") as handle:
        plistlib.dump(info, handle)

    executable = app_dir / "Contents" / "MacOS" / info["CFBundleExecutable"]
    if not executable.is_file():
        raise SystemExit("PyInstaller did not create the app executable: {}".format(executable))
    executable.chmod(executable.stat().st_mode | 0o111)
    return app_dir, manifest


def main():
    app_dir, manifest = build_app()
    print("Built native {} app: {}".format(platform.machine(), app_dir))
    print("Version {} · {} tools · build {}".format(
        APP_VERSION,
        sum(1 for name in manifest["files"] if name.startswith("tools/")),
        manifest["build"],
    ))
    print("This is a native test build. Use macos/merge_universal_app.py for the release ZIP.")


if __name__ == "__main__":
    main()
