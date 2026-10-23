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

    def entry_kind(app_path, relative):
        path = app_path / relative
        if path.is_symlink():
            return "symlink"
        if path.is_dir():
            return "directory"
        return "file"

    def resolved_binary(app_path, relative):
        path = (app_path / relative).resolve(strict=True)
        if os.path.commonpath((str(app_path.resolve()), str(path))) != str(app_path.resolve()):
            raise ValueError("A native library resolves outside its app bundle: {}".format(relative))
        if not path.is_file() or not is_macho(path):
            raise ValueError("A native library link does not resolve to Mach-O code: {}".format(relative))
        return path

    special_frameworks = {}
    x86_frameworks = x86_app / "Contents" / "Frameworks"
    arm_frameworks = arm_app / "Contents" / "Frameworks"
    for name in sorted({path.name for path in x86_frameworks.glob("*.dylib")}
                       | {path.name for path in arm_frameworks.glob("*.dylib")}):
        relative = Path("Contents") / "Frameworks" / name
        x86_path = x86_app / relative
        arm_path = arm_app / relative
        if not (x86_path.exists() or x86_path.is_symlink()):
            continue
        if not (arm_path.exists() or arm_path.is_symlink()):
            continue
        x86_binary = resolved_binary(x86_app, relative)
        arm_binary = resolved_binary(arm_app, relative)
        x86_relative = x86_binary.relative_to(x86_app.resolve())
        arm_relative = arm_binary.relative_to(arm_app.resolve())
        if x86_relative != arm_relative:
            special_frameworks[relative] = (x86_binary, arm_binary)

    special_paths = set(special_frameworks)
    special_entry_names = {relative.as_posix() for relative in special_paths}
    special_resource_links = {
        (Path("Contents") / "Resources" / relative.name).as_posix()
        for relative in special_paths
        if ((x86_app / "Contents" / "Resources" / relative.name).is_symlink()
            or (arm_app / "Contents" / "Resources" / relative.name).is_symlink())
    }

    for relative in x86_entries & arm_entries:
        if entry_kind(x86_app, relative) != entry_kind(arm_app, relative):
            if relative not in special_entry_names:
                raise ValueError("The Intel and Apple Silicon app bundles disagree at {}.".format(relative))
        elif entry_kind(x86_app, relative) == "file":
            x86_macho = is_macho(x86_app / relative)
            arm_macho = is_macho(arm_app / relative)
            if x86_macho != arm_macho and relative not in special_entry_names:
                raise ValueError("The Intel and Apple Silicon app entries disagree at {}.".format(relative))
    for relative in x86_links.keys() & arm_links.keys():
        if x86_links[relative] != arm_links[relative]:
            if relative not in special_entry_names and relative not in special_resource_links:
                raise ValueError("The Intel and Apple Silicon app bundles have conflicting links at {}.".format(relative))

    if output_app.exists():
        shutil.rmtree(output_app)
    output_app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(arm_app, output_app, symlinks=True)

    for relative in sorted(x86_entries - arm_entries, key=lambda item: (len(Path(item).parts), item)):
        source = x86_app / relative
        target = output_app / relative
        if source.is_symlink():
            if target.exists() or target.is_symlink():
                raise ValueError("Cannot combine architecture-specific app entries at {}.".format(relative))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(source), target)
        elif source.is_dir():
            if target.exists() and not target.is_dir():
                raise ValueError("Cannot combine architecture-specific app entries at {}.".format(relative))
            target.mkdir(parents=True, exist_ok=True)
        else:
            if target.exists() or target.is_symlink():
                raise ValueError("Cannot combine architecture-specific app entries at {}.".format(relative))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def is_platform_specific_tkdnd_binary(relative):
        parts = relative.parts
        return (
            parts[:4] == ("Contents", "Frameworks", "tkinterdnd2", "tkdnd")
            and parts[4:5] in {("osx-arm64",), ("osx-x86_64",)}
        )

    def native_binaries(app_path):
        return {
            path.relative_to(app_path)
            for path in app_path.rglob("*")
            if path.is_file() and not path.is_symlink()
            and path.relative_to(app_path) not in special_paths and is_macho(path)
            and not is_platform_specific_tkdnd_binary(path.relative_to(app_path))
        }

    x86_binaries = native_binaries(x86_app)
    arm_binaries = native_binaries(arm_app)
    if not x86_binaries or not arm_binaries:
        raise ValueError("Both native app builds must contain Mach-O binaries.")

    def require_architecture(binary, relative, architecture):
        result = subprocess.run(
            [lipo, "-archs", str(binary)],
            check=True, capture_output=True, text=True,
        )
        if architecture not in result.stdout.split():
            raise ValueError("{} is missing {}.".format(relative, architecture))

    for relative in sorted(x86_binaries):
        require_architecture(x86_app / relative, relative, "x86_64")
    for relative in sorted(arm_binaries):
        require_architecture(arm_app / relative, relative, "arm64")

    for relative in sorted(x86_binaries & arm_binaries):
        x86_binary = x86_app / relative
        arm_binary = arm_app / relative
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

    for relative, (x86_binary, arm_binary) in sorted(special_frameworks.items()):
        require_architecture(x86_binary, relative, "x86_64")
        require_architecture(arm_binary, relative, "arm64")
        output = output_app / relative
        if output.is_symlink():
            output.unlink()
        temporary = output.with_name(output.name + ".universal")
        subprocess.run(
            [lipo, "-create", str(x86_binary), str(arm_binary), "-output", str(temporary)],
            check=True,
        )
        temporary.chmod(stat.S_IMODE(x86_binary.stat().st_mode))
        temporary.replace(output)
        result = subprocess.run(
            [lipo, "-archs", str(output)],
            check=True, capture_output=True, text=True,
        )
        if not {"x86_64", "arm64"}.issubset(result.stdout.split()):
            raise ValueError("{} is not a universal framework library.".format(relative))

        resource_link = output_app / "Contents" / "Resources" / relative.name
        if resource_link.is_symlink():
            resource_link.unlink()
            os.symlink("../Frameworks/{}".format(relative.name), resource_link)

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
