import hashlib
import json
import os
import plistlib
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import certifi

from app_version import APP_VERSION


REPOSITORY = "CWrath/autoae"
API_URL = "https://api.github.com/repos/{}/releases/latest".format(REPOSITORY)
ASSET_NAMES = {
    "win32": "AutoAE-Setup.exe",
    "darwin": "AutoAE-macos.zip",
}
MAX_RELEASE_SIZE = 2 * 1024 * 1024 * 1024
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _version_tuple(version):
    match = _VERSION_PATTERN.fullmatch(str(version).strip())
    if not match:
        raise ValueError("Version must use the format vMAJOR.MINOR.PATCH.")
    return tuple(int(part) for part in match.groups())


def _open_url(request, timeout):
    """Use a bundled CA store instead of relying on the frozen Python's system paths."""
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def check_for_update(current_version=APP_VERSION, system=None, opener=None):
    """Return the latest compatible release, or None when already up to date."""
    system = system or sys.platform
    asset_name = ASSET_NAMES.get(system)
    if not asset_name:
        raise RuntimeError("Automatic updates are not supported on this platform.")

    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AutoAE-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with (opener or _open_url)(request, timeout=30) as response:
            release = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                "No published Auto AE release was found at github.com/{}.".format(REPOSITORY)
            ) from exc
        raise RuntimeError("GitHub update check failed (HTTP {}).".format(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Couldn't check GitHub for updates: {}".format(exc)) from exc

    tag = release.get("tag_name", "")
    try:
        latest_version = _version_tuple(tag)
        installed_version = _version_tuple(current_version)
    except ValueError as exc:
        raise RuntimeError("The latest GitHub release has an invalid version tag.") from exc

    if latest_version <= installed_version:
        return None

    assets = release.get("assets", [])
    asset = next((item for item in assets if item.get("name") == asset_name), None)
    if asset is None:
        raise RuntimeError(
            "Release {} is missing its required {} update file.".format(tag, asset_name)
        )
    if not isinstance(asset.get("size"), int) or not 0 < asset["size"] <= MAX_RELEASE_SIZE:
        raise RuntimeError("The release update file has an invalid or unsupported size.")

    asset_url = asset.get("browser_download_url", "")
    parsed_url = urllib.parse.urlparse(asset_url)
    if (parsed_url.scheme != "https" or parsed_url.hostname != "github.com"
            or not parsed_url.path.startswith("/{}/releases/download/".format(REPOSITORY))):
        raise RuntimeError("The release update file has an unexpected download URL.")

    return {
        "version": ".".join(str(part) for part in latest_version),
        "tag": tag,
        "name": release.get("name") or tag,
        "notes": release.get("body") or "",
        "release_url": release.get("html_url") or "",
        "asset_name": asset_name,
        "asset_url": asset_url,
        "asset_size": asset["size"],
    }


def download_release_asset(release, progress_callback=None, opener=None):
    """Download a release asset into a unique temporary folder; return its path."""
    parsed_url = urllib.parse.urlparse(release["asset_url"])
    expected_name = ASSET_NAMES.get(sys.platform)
    if release["asset_name"] != expected_name:
        raise RuntimeError("The selected update file does not match this platform.")
    if (parsed_url.scheme != "https" or parsed_url.hostname != "github.com"
            or not parsed_url.path.startswith("/{}/releases/download/".format(REPOSITORY))):
        raise RuntimeError("The release update file has an unexpected download URL.")

    request = urllib.request.Request(
        release["asset_url"], headers={"User-Agent": "AutoAE-Updater"}
    )
    folder = Path(tempfile.mkdtemp(prefix="autoae-update-"))
    target = folder / expected_name
    received = 0
    try:
        with (opener or _open_url)(request, timeout=60) as response, open(target, "wb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                received += len(block)
                if received > release["asset_size"] or received > MAX_RELEASE_SIZE:
                    raise RuntimeError("The downloaded update is larger than the published file.")
                output.write(block)
                if progress_callback:
                    progress_callback(received, release["asset_size"])
        if received != release["asset_size"]:
            raise RuntimeError(
                "The downloaded update is incomplete ({} of {} bytes).".format(
                    received, release["asset_size"]
                )
            )
        return str(target)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def stage_macos_update(archive_path, support_dir, expected_version=None):
    """Validate and stage a complete macOS app bundle without touching user settings."""
    support_dir = Path(support_dir).expanduser().resolve()
    update_dir = support_dir / "macos-update"
    staging_dir = support_dir / "macos-update-staging"
    previous_dir = support_dir / "macos-update-previous"

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    executable_modes = {}

    try:
        with zipfile.ZipFile(archive_path) as archive:
            total_size = 0
            seen = set()
            app_prefix = "Auto AE.app"
            for item in archive.infolist():
                if item.filename == "Install Auto AE README.txt":
                    continue
                relative = PurePosixPath(item.filename)
                if (relative.is_absolute() or "\\" in item.filename
                        or ":" in item.filename
                        or any(part in ("", ".", "..") for part in relative.parts)
                        or not relative.parts or relative.parts[0] != app_prefix):
                    raise RuntimeError("The macOS update archive contains an unsafe file path.")
                canonical = relative.as_posix() + ("/" if item.is_dir() else "")
                if item.filename != canonical:
                    raise RuntimeError("The macOS update archive contains a noncanonical file path.")
                if item.filename in seen:
                    raise RuntimeError("The macOS update archive contains duplicate paths.")
                seen.add(item.filename)
                total_size += item.file_size
                if total_size > MAX_RELEASE_SIZE:
                    raise RuntimeError("The macOS update archive is too large.")
                target = staging_dir.joinpath(*relative.parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                mode = item.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if file_type == stat.S_IFLNK:
                    link_target = archive.read(item).decode("utf-8")
                    if not link_target or os.path.isabs(link_target) or "\\" in link_target:
                        raise RuntimeError("The macOS update archive contains an unsafe symbolic link.")
                    resolved_link = PurePosixPath(*relative.parts[:-1], link_target)
                    normalized = []
                    for part in resolved_link.parts:
                        if part in ("", "."):
                            continue
                        if part == "..":
                            if not normalized:
                                raise RuntimeError("The macOS update archive contains an unsafe symbolic link.")
                            normalized.pop()
                        else:
                            normalized.append(part)
                    if not normalized or normalized[0] != app_prefix:
                        raise RuntimeError("The macOS update archive contains an unsafe symbolic link.")
                    os.symlink(link_target, target)
                    continue
                if file_type not in (0, stat.S_IFREG):
                    raise RuntimeError("The macOS update archive contains an unsupported file type.")
                with archive.open(item) as source, open(target, "xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(mode & 0o777 or 0o644)
                if relative.parts[:3] == (app_prefix, "Contents", "MacOS"):
                    executable_modes[relative.as_posix()] = mode & 0o777

        app_dir = staging_dir / app_prefix
        info_path = app_dir / "Contents" / "Info.plist"
        executable_dir = app_dir / "Contents" / "MacOS"
        if not info_path.is_file() or not executable_dir.is_dir():
            raise RuntimeError("The macOS update archive doesn't contain a complete Auto AE app.")
        with open(info_path, "rb") as handle:
            info = plistlib.load(handle)
        if info.get("CFBundleIdentifier") != "com.autoae.app" or info.get("CFBundlePackageType") != "APPL":
            raise RuntimeError("The macOS update archive contains an unexpected app bundle.")
        bundle_executable = info.get("CFBundleExecutable")
        if not isinstance(bundle_executable, str) or Path(bundle_executable).name != bundle_executable:
            raise RuntimeError("The macOS app bundle has an invalid executable name.")
        executable = executable_dir / bundle_executable
        executable_member = PurePosixPath(app_prefix, "Contents", "MacOS", bundle_executable).as_posix()
        if not executable.is_file() or not executable_modes.get(executable_member, 0) & 0o111:
            raise RuntimeError("The macOS app bundle is missing its executable.")
        if expected_version is not None and info.get("CFBundleShortVersionString") != expected_version:
            raise RuntimeError("The macOS app version doesn't match the published release version.")

        resources = app_dir / "Contents" / "Resources"
        bundled_resources = resources / "bundle"
        source_dir = bundled_resources / "app"
        manifest_path = bundled_resources / "app-manifest.json"
        if not source_dir.is_dir() or not manifest_path.is_file():
            raise RuntimeError("The macOS update archive is missing its app source manifest.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise RuntimeError("The macOS update archive has an invalid app manifest.")
        if manifest.get("version") != info.get("CFBundleShortVersionString"):
            raise RuntimeError("The macOS app and source manifest versions do not match.")
        if expected_version is not None and manifest.get("version") != expected_version:
            raise RuntimeError("The macOS app version doesn't match the published release version.")
        for relative_name, expected_digest in files.items():
            relative = PurePosixPath(relative_name)
            if (relative.is_absolute() or "\\" in relative_name or ":" in relative_name
                    or any(part in ("", ".", "..") for part in relative.parts)):
                raise RuntimeError("The macOS app manifest contains an unsafe file path.")
            source = source_dir.joinpath(*relative.parts)
            if (os.path.commonpath((str(source_dir.resolve()), str(source.resolve()))) != str(source_dir.resolve())
                    or not source.is_file() or _sha256(source) != expected_digest):
                raise RuntimeError("A file in the macOS update archive failed verification.")

        if previous_dir.exists():
            shutil.rmtree(previous_dir)
        if update_dir.exists():
            os.replace(update_dir, previous_dir)
        try:
            os.replace(staging_dir, update_dir)
        except OSError:
            if previous_dir.exists() and not update_dir.exists():
                os.replace(previous_dir, update_dir)
            raise
        if previous_dir.exists():
            shutil.rmtree(previous_dir)
        return str(update_dir / app_prefix)
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relaunch_macos_after_exit(bundle_path, staged_bundle, archive_path, process_id=None):
    """Replace the installed app after it exits, then reopen the updated bundle."""
    process_id = process_id or os.getpid()
    bundle_path = Path(bundle_path).resolve()
    staged_bundle = Path(staged_bundle).resolve()
    update_dir = staged_bundle.parent
    download_dir = Path(archive_path).resolve().parent
    if not bundle_path.is_dir() or not staged_bundle.is_dir():
        raise RuntimeError("The installed or staged Auto AE app bundle is missing.")
    if not os.access(str(bundle_path.parent), os.W_OK):
        raise RuntimeError(
            "Auto AE can't update in this location. Install it in your user Applications folder "
            "(~/Applications) and try again."
        )
    if update_dir.name != "macos-update":
        raise RuntimeError("The staged Auto AE update is in an unexpected location.")
    if (download_dir.parent != Path(tempfile.gettempdir()).resolve()
            or not download_dir.name.startswith("autoae-update-")):
        raise RuntimeError("The downloaded Auto AE update is in an unexpected location.")
    helper_path = update_dir.parent / "install-macos-update.sh"
    helper = """#!/bin/bash
set -euo pipefail
pid="$1"
bundle="$2"
staged="$3"
update_dir="$4"
download_dir="$5"
helper="$0"
parent="$(/usr/bin/dirname "$bundle")"
base="$(/usr/bin/basename "$bundle")"
replacement="$parent/.$base.new.$$"
backup="$parent/.$base.backup.$$"
fail() {
  /usr/bin/osascript -e 'display dialog "Auto AE could not finish installing the update. Your existing app has been kept. Please reinstall the update from the downloaded ZIP." with title "Auto AE Update" buttons {"OK"} default button "OK" with icon stop' >/dev/null 2>&1 || true
  exit 1
}
while /bin/kill -0 "$pid" 2>/dev/null; do /bin/sleep 0.5; done
/usr/bin/ditto "$staged" "$replacement" || fail
/bin/mv "$bundle" "$backup" || fail
if ! /bin/mv "$replacement" "$bundle"; then
  /bin/mv "$backup" "$bundle" || true
  fail
fi
/bin/rm -rf "$backup" "$update_dir" "$download_dir" || true
/bin/rm -f "$helper" || true
/usr/bin/open "$bundle"
"""
    helper_path.write_text(helper, encoding="utf-8", newline="\n")
    helper_path.chmod(0o700)
    subprocess.Popen(
        [
            "/bin/bash", str(helper_path), str(process_id), str(bundle_path),
            str(staged_bundle), str(update_dir), str(download_dir),
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
