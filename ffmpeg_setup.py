"""Locate FFmpeg for AUTO AE and make it available to the app.

Search order: FFmpeg already on PATH, then the copy bundled next to the app
(Windows: ffmpeg-7.1.1-essentials_build/bin, Mac: an ffmpeg-mac folder), then on a
Mac the usual Homebrew and MacPorts folders. Apps opened from Finder don't get the
Terminal's PATH, so those folders are checked directly.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

MAC_FFMPEG_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")


def _support_ffmpeg_dir() -> Path:
    """Return Auto AE's per-user FFmpeg installation directory."""
    support_dir = os.environ.get("AUTOAE_SUPPORT_DIR")
    if support_dir:
        root = Path(support_dir).expanduser()
    else:
        root = Path.home() / "Library" / "Application Support" / "AUTO_AE"
    return root / "ffmpeg"


def _application_root() -> Path:
    """Return the directory containing the source or packaged application."""
    packaged_root = getattr(sys, "_MEIPASS", None)
    if packaged_root:
        return Path(packaged_root)
    return Path(__file__).resolve().parent


def _add_to_path(bin_dir: Path, ffmpeg: Path) -> Path:
    """Put bin_dir first on PATH so tools that run "ffmpeg" or "ffprobe" by name find this copy."""
    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    return ffmpeg


def configure_ffmpeg() -> Optional[Path]:
    """Make FFmpeg available to this process and return its executable path."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return Path(system_ffmpeg)

    if os.name == "nt":
        bundled_bin = _application_root() / "ffmpeg-7.1.1-essentials_build" / "bin"
        bundled_ffmpeg = bundled_bin / "ffmpeg.exe"
        if not bundled_ffmpeg.is_file():
            return None
        return _add_to_path(bundled_bin, bundled_ffmpeg)

    # Prefer Auto AE's writable per-user copy to binaries inside the read-only app bundle.
    bundled_dirs = [_support_ffmpeg_dir(), _application_root() / "ffmpeg-mac"]
    for bin_dir in bundled_dirs + [Path(d) for d in MAC_FFMPEG_DIRS]:
        candidate = bin_dir / "ffmpeg"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return _add_to_path(bin_dir, candidate)
    return None
