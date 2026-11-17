"""File-metadata values for the filename tokens used by Auto Ingest and the Batch Renamer."""
import json
import os
import shutil
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path

SOURCE_EXTENSIONS = {
    "audio": {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".aif", ".aiff", ".wma", ".opus"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mxf", ".mts", ".m2ts"},
    "image": {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"},
}
# kind -> (label, what it inserts)
METADATA_TOKENS = {
    "filetype": ("File type", "Video, Audio or Image, from the extension"),
    "created": ("Date created", "When the file was created (date format below)"),
    "modified": ("Date modified", "When the file was last modified (date format below)"),
    "resolution": ("Resolution", "Video size, e.g. 1920x1080 (needs ffprobe)"),
    "framerate": ("Frame rate", "Video frame rate, e.g. 23.98fps (needs ffprobe)"),
    "codec": ("Video codec", "Video codec, e.g. H264 or ProRes (needs ffprobe)"),
    "timecode": ("Start timecode", "The embedded start timecode without separators, e.g. 01020304 (needs ffprobe)"),
    "audiochannels": ("Audio channels", "Mono, Stereo, 5.1, 7.1 or Atmos (needs ffprobe)"),
}
PROBE_KINDS = {"resolution", "framerate", "codec", "timecode", "audiochannels"}
CHANNEL_NAMES = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}
CODEC_NAMES = {"hevc": "HEVC", "prores": "ProRes", "dnxhd": "DNxHD", "mpeg4": "MPEG4", "mjpeg": "MJPEG"}


def file_category(path):
    extension = Path(path).suffix.lower()
    for category, extensions in SOURCE_EXTENSIONS.items():
        if extension in extensions:
            return category
    return ""


def _audio_layout(streams):
    """Mono/Stereo/5.1/7.1/Atmos from the widest audio stream; blank when there is no audio."""
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        return ""
    if any("atmos" in str(s.get("profile", "")).lower() for s in audio):
        return "Atmos"
    channels = max(int(s.get("channels") or 0) for s in audio)
    return CHANNEL_NAMES.get(channels, f"{channels}ch" if channels else "")


def _timecode(info):
    tags = [info.get("format", {}).get("tags", {})] + [s.get("tags", {}) for s in info.get("streams", [])]
    for tag in tags:
        value = next((v for k, v in tag.items() if k.lower() == "timecode"), "")
        if value:
            return str(value).replace(":", "").replace(";", "").replace("-", "")
    return ""


def _find_ffprobe():
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        try:
            from ffmpeg_setup import configure_ffmpeg
            ffmpeg = configure_ffmpeg() or shutil.which("ffmpeg")
        except Exception:
            ffmpeg = None
        if ffmpeg:
            sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            ffprobe = str(sibling) if sibling.is_file() else None
    return ffprobe


def probe_media(path):
    """ffprobe-based token values (cached per file version); blank when unavailable."""
    path = Path(path)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {kind: "" for kind in PROBE_KINDS}
    return dict(_probe_cached(str(path), stamp))


@lru_cache(maxsize=512)
def _probe_cached(path, _stamp):
    return _run_ffprobe(path)


def _run_ffprobe(path):
    blank = {kind: "" for kind in PROBE_KINDS}
    ffprobe = _find_ffprobe()
    if not ffprobe:
        return blank
    options = {"stdin": subprocess.DEVNULL, "capture_output": True, "text": True, "timeout": 30}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,profile,width,height,avg_frame_rate,channels:"
             "stream_tags=timecode:format_tags=timecode",
             "-of", "json", str(path)],
            **options,
        )
        info = json.loads(result.stdout or "{}")
        streams = info.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
    except (OSError, ValueError, subprocess.SubprocessError):
        return blank
    values = dict(blank, audiochannels=_audio_layout(streams), timecode=_timecode(info))
    if video.get("width") and video.get("height"):
        values["resolution"] = f"{video['width']}x{video['height']}"
    try:
        numerator, _, denominator = str(video.get("avg_frame_rate", "0/1")).partition("/")
        rate = float(numerator) / float(denominator or 1)
        if rate > 0:
            values["framerate"] = f"{rate:.2f}".rstrip("0").rstrip(".") + "fps"
    except (ValueError, ZeroDivisionError):
        pass
    codec = str(video.get("codec_name", ""))
    values["codec"] = CODEC_NAMES.get(codec, codec.upper())
    return values


def metadata_values(path, date_format, kinds=None):
    """Values for every metadata token; ffprobe runs only when one of `kinds` needs it."""
    path = Path(path)
    stat = path.stat()
    # st_birthtime is the true creation time on macOS; Windows reports it as st_ctime.
    created = getattr(stat, "st_birthtime", None) or stat.st_ctime
    values = {
        "filetype": file_category(path).capitalize(),
        "created": datetime.fromtimestamp(created).strftime(date_format),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime(date_format),
        **{kind: "" for kind in PROBE_KINDS},
    }
    if kinds is None or PROBE_KINDS & set(kinds):
        values.update(probe_media(path))
    return values
