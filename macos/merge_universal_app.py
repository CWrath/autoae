#!/usr/bin/env python3
"""Merge native Intel and Apple Silicon Auto AE bundles into a universal app."""

import argparse
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Auto AE"
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


def is_macho(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(4) in MACHO_MAGICS
    except OSError:
        return False


def source_manifest(app_path):
    manifest_path = (
        app_path / "Contents" / "Resources" / "bundle" / "app-manifest.json"
    )
    if not manifest_path.is_file():
        raise ValueError("Missing bundled app manifest: {}".format(manifest_path))
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def bundle_version(app_path):
    info_path = app_path / "Contents" / "Info.plist"
    with open(info_path, "rb") as handle:
        return plistlib.load(handle).get("CFBundleShortVersionString")


def merge_apps(x86_app, arm_app, output_app, expected_version=None):
    x86_app, arm_app, output_app = map(Path, (x86_app, arm_app, output_app))
    if sys.platform != "darwin":
        raise SystemExit("Merge universal macOS apps on macOS.")
    lipo = shutil.which("lipo")
    if not lipo:
        raise SystemExit("Xcode Command Line Tools are required (lipo was not found).")
    if x86_app.resolve() == arm_app.resolve():
        raise ValueError("Intel and Apple Silicon builds must come from separate native runners.")
    if not x86_app.is_dir() or not arm_app.is_dir():
        raise FileNotFoundError("Both Intel and Apple Silicon app bundles are required.")

    x86_manifest = source_manifest(x86_app)
    arm_manifest = source_manifest(arm_app)
    if x86_manifest.get("files") != arm_manifest.get("files"):
        raise ValueError("The Intel and Apple Silicon builds contain different app source files.")
    if x86_manifest.get("version") != arm_manifest.get("version"):
        raise ValueError("The Intel and Apple Silicon builds have different versions.")
    version = x86_manifest.get("version")
    if expected_version and version != expected_version:
        raise ValueError("Expected app version {}, found {}.".format(expected_version, version))
    if bundle_version(x86_app) != version or bundle_version(arm_app) != version:
        raise ValueError("The app bundle version does not match its source manifest.")

    def bundle_entries(app_path):
        entries = set()
        links = {}
        for path in app_path.rglob("*"):
            relative = path.relative_to(app_path).as_posix()
            entries.add(relative)
            if path.is_symlink():
                links[relative] = os.readlink(path)
        return entries, links

    x86_entries, x86_links = bundle_entries(x86_app)
    arm_entries, arm_links = bundle_entries(arm_app)
    if x86_entries != arm_entries or x86_links != arm_links:
        raise ValueError("The Intel and Apple Silicon app bundles have different layouts.")

    x86_binaries = {
        path.relative_to(x86_app)
        for path in x86_app.rglob("*")
        if path.is_file() and not path.is_symlink() and is_macho(path)
    }
    arm_binaries = {
        path.relative_to(arm_app)
        for path in arm_app.rglob("*")
        if path.is_file() and not path.is_symlink() and is_macho(path)
    }
    if not x86_binaries or x86_binaries != arm_binaries:
        missing_arm = sorted(str(path) for path in x86_binaries - arm_binaries)
        missing_x86 = sorted(str(path) for path in arm_binaries - x86_binaries)
        raise ValueError(
            "Native binary sets differ between builds (Intel-only: {}; Apple-Silicon-only: {}).".format(
                ", ".join(missing_arm) or "none",
                ", ".join(missing_x86) or "none",
            )
        )

    if output_app.exists():
        shutil.rmtree(output_app)
    output_app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(arm_app, output_app, symlinks=True)
    for relative in sorted(x86_binaries):
        x86_binary = x86_app / relative
        arm_binary = arm_app / relative
        for binary, architecture in ((x86_binary, "x86_64"), (arm_binary, "arm64")):
            result = subprocess.run(
                [lipo, "-archs", str(binary)],
                check=True, capture_output=True, text=True,
            )
            if architecture not in result.stdout.split():
                raise ValueError("{} is missing {}.".format(relative, architecture))

        output = output_app / relative
        temporary = output.with_name(output.name + ".universal")
        subprocess.run(
            [lipo, "-create", str(x86_binary), str(arm_binary), "-output", str(temporary)],
            check=True,
        )
        temporary.chmod(stat.S_IMODE(output.stat().st_mode))
        temporary.replace(output)
        result = subprocess.run(
            [lipo, "-archs", str(output)],
            check=True, capture_output=True, text=True,
        )
        architectures = set(result.stdout.split())
        if not {"x86_64", "arm64"}.issubset(architectures):
            raise ValueError("{} is not universal.".format(relative))

    return output_app


def zip_app(app_path, output_path, readme_path):
    app_path, output_path, readme_path = map(Path, (app_path, output_path, readme_path))
    if output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.localtime()[:6]
    app_root = app_path.resolve()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted([app_path] + list(app_path.rglob("*"))):
            name = path.relative_to(app_path.parent).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                resolved = (path.parent / target).resolve()
                if os.path.commonpath((str(app_root), str(resolved))) != str(app_root):
                    raise ValueError("App bundle contains a symlink outside the bundle: {}".format(path))
                info = zipfile.ZipInfo(name, date_time=now)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, target.encode("utf-8"))
            elif path.is_dir():
                info = zipfile.ZipInfo(name + "/", date_time=now)
                info.create_system = 3
                info.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
                archive.writestr(info, b"")
            else:
                info = zipfile.ZipInfo(name, date_time=now)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | stat.S_IMODE(path.stat().st_mode)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())

        info = zipfile.ZipInfo(readme_path.name, date_time=now)
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, readme_path.read_bytes())
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--x86-app", required=True, type=Path)
    parser.add_argument("--arm-app", required=True, type=Path)
    parser.add_argument("--output-app", required=True, type=Path)
    parser.add_argument("--output-zip", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()

    merged = merge_apps(
        args.x86_app, args.arm_app, args.output_app,
        expected_version=args.expected_version,
    )
    zip_app(merged, args.output_zip, ROOT / "macos" / "Install Auto AE README.txt")
    print("Built universal Auto AE {}: {}".format(args.expected_version, args.output_zip))


if __name__ == "__main__":
    main()
