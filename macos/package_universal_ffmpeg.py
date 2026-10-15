#!/usr/bin/env python3
"""Merge native FFmpeg builds and package their LGPL notices and source."""

import argparse
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

VERSION = "9.0.2"
BINARIES = ("ffmpeg", "ffprobe")
SUPPORT_FILES = ("COPYING.LGPLv2.1", "THIRD-PARTY-NOTICES.txt")


def package(
    intel_dir, arm_dir, output_zip, source_output, notices_path, license_path,
    signing_identity,
):
    intel_dir, arm_dir, output_zip, source_output, notices_path = map(
        Path, (intel_dir, arm_dir, output_zip, source_output, notices_path)
    )
    license_path = Path(license_path)
    lipo = shutil.which("lipo")
    if not lipo:
        raise SystemExit("Xcode Command Line Tools are required (lipo was not found).")

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    source_output.parent.mkdir(parents=True, exist_ok=True)
    source_name = "ffmpeg-{}.tar.xz".format(VERSION)
    intel_source, arm_source = intel_dir / source_name, arm_dir / source_name
    if not intel_source.is_file() or not arm_source.is_file():
        raise FileNotFoundError("The native FFmpeg source archives are missing.")
    if intel_source.read_bytes() != arm_source.read_bytes():
        raise ValueError("Native FFmpeg builds used different source archives.")
    shutil.copyfile(intel_source, source_output)

    merged = output_zip.parent / "ffmpeg-universal"
    if merged.exists():
        shutil.rmtree(merged)
    merged.mkdir()
    try:
        for name in BINARIES:
            intel_binary, arm_binary = intel_dir / "bin" / name, arm_dir / "bin" / name
            if not intel_binary.is_file() or not arm_binary.is_file():
                raise FileNotFoundError("Both native {} binaries are required.".format(name))
            for binary, architecture in ((intel_binary, "x86_64"), (arm_binary, "arm64")):
                result = subprocess.run(
                    [lipo, "-archs", str(binary)], check=True, capture_output=True, text=True
                )
                if architecture not in result.stdout.split():
                    raise ValueError("{} is missing {}.".format(binary, architecture))

            output = merged / name
            subprocess.run(
                [lipo, "-create", str(intel_binary), str(arm_binary), "-output", str(output)],
                check=True,
            )
            output.chmod(stat.S_IMODE(intel_binary.stat().st_mode) | 0o111)
            subprocess.run(
                [
                    "codesign", "--force", "--timestamp", "--options", "runtime",
                    "--sign", signing_identity, "--keychain", os.environ["KEYCHAIN_PATH"],
                    str(output),
                ],
                check=True,
            )
            subprocess.run(["codesign", "--verify", "--strict", str(output)], check=True)
            result = subprocess.run(
                [lipo, "-archs", str(output)], check=True, capture_output=True, text=True
            )
            if not {"x86_64", "arm64"}.issubset(result.stdout.split()):
                raise ValueError("{} is not universal.".format(name))

        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            contents = (
                [(name, merged / name) for name in BINARIES]
                + [("THIRD-PARTY-NOTICES.txt", notices_path)]
                + [("COPYING.LGPLv2.1", license_path)]
            )
            for name, path in contents:
                archive.write(path, name)
    finally:
        shutil.rmtree(merged, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--intel-dir", required=True, type=Path)
    parser.add_argument("--arm-dir", required=True, type=Path)
    parser.add_argument("--output-zip", required=True, type=Path)
    parser.add_argument("--source-output", required=True, type=Path)
    parser.add_argument("--notices", required=True, type=Path)
    parser.add_argument("--license", required=True, type=Path)
    parser.add_argument("--signing-identity", required=True)
    args = parser.parse_args()
    package(
        args.intel_dir, args.arm_dir, args.output_zip,
        args.source_output, args.notices, args.license, args.signing_identity,
    )


if __name__ == "__main__":
    main()
