"""Download and install the verified macOS FFmpeg tools for Auto AE."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import app_updater

ASSET_NAME = "AutoAE-ffmpeg-macos.zip"
MAX_DOWNLOAD_SIZE = 256 * 1024 * 1024
MAX_UNPACKED_SIZE = 256 * 1024 * 1024
BINARIES = ("ffmpeg", "ffprobe")
SUPPORT_FILES = ("COPYING.LGPLv2.1", "THIRD-PARTY-NOTICES.txt")
_SHA256_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")


def support_dir():
    configured = os.environ.get("AUTOAE_SUPPORT_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Application Support" / "AUTO_AE"


def _latest_asset(opener=None):
    request = urllib.request.Request(
        app_updater.API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AutoAE-FFmpeg-Installer",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with (opener or app_updater._open_url)(request, timeout=30) as response:
            release = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Couldn't check the Auto AE release for FFmpeg (HTTP {}).".format(
            exc.code
        )) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Couldn't check for the FFmpeg download: {}".format(exc)) from exc

    asset = next(
        (item for item in release.get("assets", []) if item.get("name") == ASSET_NAME),
        None,
    )
    if asset is None:
        raise RuntimeError(
            "The latest Auto AE release does not include its FFmpeg installer yet. "
            "Try again after updating Auto AE."
        )
    match = _SHA256_PATTERN.fullmatch(str(asset.get("digest", "")))
    if not match:
        raise RuntimeError("The FFmpeg download has no valid SHA-256 checksum.")
    size = asset.get("size")
    if not isinstance(size, int) or not 0 < size <= MAX_DOWNLOAD_SIZE:
        raise RuntimeError("The FFmpeg download has an invalid or unsupported size.")

    url = asset.get("browser_download_url", "")
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != "github.com"
            or not parsed.path.startswith(
                "/{}/releases/download/".format(app_updater.REPOSITORY)
            )):
        raise RuntimeError("The FFmpeg download has an unexpected URL.")
    return url, size, match.group(1)


def _download(url, size, expected_digest, target, progress_callback, opener):
    request = urllib.request.Request(url, headers={"User-Agent": "AutoAE-FFmpeg-Installer"})
    digest = hashlib.sha256()
    received = 0
    try:
        with (opener or app_updater._open_url)(request, timeout=60) as response, open(
            target, "wb"
        ) as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                received += len(block)
                if received > size or received > MAX_DOWNLOAD_SIZE:
                    raise RuntimeError("The FFmpeg download is larger than its published size.")
                digest.update(block)
                output.write(block)
                if progress_callback:
                    progress_callback(received, size)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Couldn't download FFmpeg: {}".format(exc)) from exc
    if received != size:
        raise RuntimeError(
            "The FFmpeg download is incomplete ({} of {} bytes).".format(received, size)
        )
    if digest.hexdigest() != expected_digest:
        raise RuntimeError("The FFmpeg download failed its SHA-256 integrity check.")


def _extract_tools(archive_path, target_dir):
    with zipfile.ZipFile(archive_path) as archive:
        entries = {}
        total_size = 0
        for item in archive.infolist():
            relative = PurePosixPath(item.filename)
            if (relative.is_absolute() or "\\" in item.filename or ":" in item.filename
                    or len(relative.parts) != 1
                    or relative.parts[0] not in BINARIES + SUPPORT_FILES
                    or item.is_dir() or item.filename in entries):
                raise RuntimeError("The FFmpeg archive contains an unexpected file path.")
            total_size += item.file_size
            if total_size > MAX_UNPACKED_SIZE:
                raise RuntimeError("The FFmpeg archive is larger than the supported limit.")
            entries[item.filename] = item

        if set(entries) != set(BINARIES + SUPPORT_FILES):
            raise RuntimeError(
                "The FFmpeg archive must contain ffmpeg, ffprobe, and their license notices."
            )
        for name, item in entries.items():
            target = target_dir / name
            with archive.open(item) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)
            if name in BINARIES:
                target.chmod(0o755)


def _verify_tools(directory):
    if sys.platform != "darwin":
        raise RuntimeError("The in-app FFmpeg installer is available only on macOS.")
    for name in BINARIES:
        try:
            subprocess.run(
                ["/usr/bin/codesign", "--verify", "--strict", str(directory / name)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            subprocess.run(
                [str(directory / name), "-version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("The downloaded {} tool could not be verified: {}".format(
                name, exc
            )) from exc


def install_ffmpeg(progress_callback=None, opener=None, destination=None):
    """Install verified FFmpeg tools into Auto AE's writable support directory."""
    if sys.platform != "darwin":
        raise RuntimeError("The in-app FFmpeg installer is available only on macOS.")

    url, size, digest = _latest_asset(opener)
    support = Path(destination) if destination is not None else support_dir()
    support.mkdir(parents=True, exist_ok=True)
    installed = support / "ffmpeg"
    backup = support / ".ffmpeg-previous"

    with tempfile.TemporaryDirectory(prefix=".ffmpeg-install-", dir=support) as temporary:
        temporary = Path(temporary)
        archive_path = temporary / ASSET_NAME
        staging = temporary / "bin"
        staging.mkdir()
        _download(url, size, digest, archive_path, progress_callback, opener)
        _extract_tools(archive_path, staging)
        _verify_tools(staging)

        if backup.exists():
            shutil.rmtree(backup)
        had_install = installed.exists()
        if had_install:
            os.replace(installed, backup)
        try:
            os.replace(staging, installed)
        except OSError:
            if had_install and backup.exists():
                os.replace(backup, installed)
            raise
        if backup.exists():
            try:
                shutil.rmtree(backup)
            except OSError as exc:
                raise RuntimeError(
                    "FFmpeg was installed, but the previous copy could not be removed: "
                    "{}".format(backup)
                ) from exc

    return installed / "ffmpeg"
