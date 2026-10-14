import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
from tkinter import filedialog, messagebox
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import platform
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk
from base_tool import BaseTool
from ffmpeg_setup import configure_ffmpeg
import theme
from theme import C


# ---------------------------------------------------------------------------------------------
# What counts as media. Known extensions show up straight away; any other file is checked with
# ffprobe in the background, so a renamed clip, a camera file with no extension or audio saved
# as .dat still appears. Documents and project files are skipped without probing.
# ---------------------------------------------------------------------------------------------
VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".qt", ".avi", ".mkv", ".wmv", ".asf", ".flv", ".f4v", ".webm", ".mxf",
    ".mts", ".m2ts", ".ts", ".mpg", ".mpeg", ".m2v", ".vob", ".3gp", ".3g2", ".ogv", ".dv", ".y4m",
    ".h264", ".264", ".h265", ".265", ".hevc", ".nut", ".rm", ".rmvb", ".divx", ".r3d", ".braw",
    ".ari", ".crm", ".mj2",
}
AUDIO_EXTENSIONS = {
    ".wav", ".wave", ".bwf", ".rf64", ".w64", ".aif", ".aiff", ".aifc", ".caf", ".mp3", ".mp2",
    ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".ac3", ".eac3", ".dts",
    ".mka", ".amr", ".ape", ".wv", ".au", ".snd",
}
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif",
    ".avif", ".jxl", ".psd", ".tga", ".dpx", ".exr", ".hdr", ".jp2", ".j2k", ".ico", ".ppm",
    ".pgm", ".pbm", ".sgi", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef",
    ".srw",
}
NOT_MEDIA_EXTENSIONS = {
    ".txt", ".md", ".rtf", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx",
    ".pages", ".numbers", ".key", ".json", ".xml", ".html", ".htm", ".css", ".js", ".py", ".pyc",
    ".ini", ".cfg", ".log", ".plist", ".edl", ".ale", ".aaf", ".omf", ".fcpxml", ".xmp", ".srt",
    ".vtt", ".scc", ".stl", ".prproj", ".prel", ".avb", ".avp", ".avs", ".mxfx", ".drp", ".drt",
    ".aep", ".mogrt", ".cube", ".3dl", ".lut", ".kys", ".ave", ".pek", ".pkf", ".cfa", ".cache",
    ".db", ".sqlite", ".lnk", ".url", ".exe", ".dll", ".msi", ".bat", ".cmd", ".sh", ".app",
    ".dmg", ".pkg", ".iso", ".zip", ".rar", ".7z", ".tar", ".gz", ".tmp", ".bak", ".part",
}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS
STILL_CODECS = {"png", "mjpeg", "jpegls", "jpeg2000", "bmp", "tiff", "webp", "gif", "exr", "dpx",
                "targa", "psd", "sgi", "hdr", "ppm", "pgm", "pbm", "pam", "qoi", "jpegxl", "photocd"}
MAX_PROBED_FILES = 400          # per folder listing, for files with unknown extensions
NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # keep FFmpeg from flashing console windows


def extension_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return None


def is_hidden(name):
    """Dotfiles, and the ._ files macOS leaves on shared drives next to every clip."""
    return name.startswith(".") or name.lower() in ("thumbs.db", "desktop.ini")


# ---------------------------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------------------------
_tools = {}


def ffmpeg_tools():
    """(ffmpeg, ffprobe) paths, or None for any that can't be found."""
    if not _tools:
        ffmpeg = configure_ffmpeg()
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None and ffmpeg is not None:
            sibling = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            ffprobe = str(sibling) if sibling.is_file() else None
        _tools["ffmpeg"] = str(ffmpeg) if ffmpeg else None
        _tools["ffprobe"] = ffprobe
    return _tools["ffmpeg"], _tools["ffprobe"]


def _number(value, default=None):
    try:
        number = float(value)
        return number if number == number and number > 0 else default   # rejects NaN, 0, negatives
    except (TypeError, ValueError):
        return default


def _rate(text):
    try:
        top, bottom = str(text).split("/")
        return float(top) / float(bottom) if float(bottom) else None
    except (ValueError, ZeroDivisionError):
        return _number(text)


_probe_cache = {}
_probe_lock = threading.Lock()


def probe(path):
    """What ffprobe finds inside a file: kind (video/audio/image/None), duration, fps, size..."""
    try:
        stat = os.stat(path)
    except OSError:
        return {"kind": None}
    key = (path, stat.st_mtime, stat.st_size)
    with _probe_lock:
        if key in _probe_cache:
            return _probe_cache[key]
    info = {"kind": None}
    _, ffprobe = ffmpeg_tools()
    if ffprobe and stat.st_size > 0:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries",
                 "format=format_name,duration:stream=codec_type,codec_name,width,height,avg_frame_rate,"
                 "r_frame_rate,nb_frames,duration,sample_rate,channels,bits_per_raw_sample,"
                 "bits_per_sample:stream_disposition=attached_pic", "-of", "json", path],
                capture_output=True, timeout=15, creationflags=NO_WINDOW)
            info = _classify(json.loads(result.stdout or b"{}"))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    with _probe_lock:
        _probe_cache[key] = info
    return info


def _classify(data):
    fmt = data.get("format", {})
    format_name = fmt.get("format_name", "")
    streams = data.get("streams", [])
    if format_name in ("tty", "srt", "webvtt", "ass") or not streams:
        return {"kind": None}
    video = [s for s in streams if s.get("codec_type") == "video"
             and not s.get("disposition", {}).get("attached_pic")      # album art in an mp3/m4a
             and s.get("codec_name") not in ("ansi", "bintext", "xbin", "idf")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = _number(fmt.get("duration")) or max(
        [_number(s.get("duration"), 0) for s in streams] + [0]) or None
    info = {"kind": None, "duration": duration, "format": format_name.split(",")[0]}
    if audio:
        a = audio[0]
        info.update(audio_streams=len(audio), audio_codec=a.get("codec_name"), sample_rate=_number(a.get("sample_rate")),
                    channels=a.get("channels"),
                    bits=_number(a.get("bits_per_raw_sample")) or _number(a.get("bits_per_sample")))
    if video:
        v = video[0]
        frames = _number(v.get("nb_frames"))
        still = (format_name.endswith("_pipe") or format_name.startswith("image2")
                 or (v.get("codec_name") in STILL_CODECS and (frames or 1) <= 1 and not duration))
        fps = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))
        info.update(kind="image" if still else "video", codec=v.get("codec_name"),
                    width=v.get("width"), height=v.get("height"), fps=fps, frames=frames)
    elif audio:
        info["kind"] = "audio"
    return info


def ffmpeg_grab(path, seconds=0.0, max_width=None):
    """One frame as a PIL image, for anything Pillow or OpenCV can't read (DPX, EXR, odd codecs)."""
    ffmpeg, _ = ffmpeg_tools()
    if not ffmpeg:
        return None
    command = [ffmpeg, "-v", "error"]
    if seconds:
        command += ["-ss", "{:.3f}".format(seconds)]
    command += ["-i", path, "-frames:v", "1", "-an", "-sn"]
    if max_width:
        command += ["-vf", "scale='min({},iw)':-2".format(max_width)]
    command += ["-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, creationflags=NO_WINDOW)
        if result.stdout:
            image = Image.open(io.BytesIO(result.stdout))
            image.load()
            return image.convert("RGB")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


LONG_AUDIO_SECONDS = 30 * 60   # previews longer than this are made as MP3 rather than a huge WAV


def audio_decode_args(info):
    """FFmpeg input mapping that mixes every audio stream (camera/MXF files often put each channel
    in its own stream) down to stereo."""
    streams = int(info.get("audio_streams") or 1)
    if streams > 1:
        inputs = "".join("[0:a:{}]".format(i) for i in range(min(streams, 16)))
        return ["-filter_complex", "{}amerge=inputs={}[mix]".format(inputs, min(streams, 16)), "-map", "[mix]"]
    return ["-map", "0:a:0"]


def audio_peaks(path, info, buckets=1600):
    """Loudest sample per slice of the file (0..1), for drawing a waveform. None if it can't be read."""
    ffmpeg, _ = ffmpeg_tools()
    if not ffmpeg:
        return None
    duration = info.get("duration") or 0
    rate = 8000 if duration < 20 * 60 else 2000          # plenty for a picture, and fast on long files
    command = [ffmpeg, "-v", "error", "-i", path] + audio_decode_args(info) + [
        "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=600, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    samples = np.frombuffer(result.stdout[: len(result.stdout) // 2 * 2], dtype=np.int16)
    if samples.size == 0:
        return None
    buckets = max(1, min(buckets, samples.size))
    usable = samples[: samples.size // buckets * buckets].reshape(buckets, -1)
    peaks = np.abs(usable.astype(np.int32)).max(axis=1) / 32768.0
    top = float(peaks.max()) or 1.0
    return (peaks / max(top, 0.05)).clip(0, 1).tolist()    # normalised so quiet files still read


def load_still(path, max_size=None):
    """Any still image: Pillow first, then FFmpeg (DPX, EXR, HEIC, camera files where it can)."""
    image = None
    try:
        with Image.open(path) as opened:
            opened.draft("RGB", max_size) if max_size else None   # fast JPEG downscale
            opened.load()
            image = opened.convert("RGB")
    except Exception:
        image = ffmpeg_grab(path, max_width=max_size[0] if max_size else None)
    if image is not None and max_size:
        image.thumbnail(max_size)
    return image


# ---------------------------------------------------------------------------------------------
# Video sources. OpenCV is fast for everyday codecs; FFmpeg decodes the rest.
# Both hand back BGR numpy frames.
# ---------------------------------------------------------------------------------------------
class OpenCVVideo:
    def __init__(self, path, info=None):
        self.path = path
        self.cap = cv2.VideoCapture(path)
        ok, frame = self.cap.read() if self.cap.isOpened() else (False, None)
        if not ok or frame is None:
            self.cap.release()
            raise ValueError("OpenCV can't decode this file")
        info = info or {}
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or info.get("fps") or 25.0
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if self.frame_count <= 0 and info.get("duration"):
            self.frame_count = max(1, int(round(info["duration"] * self.fps)))
        self.frame_count = max(1, self.frame_count)
        # Some containers open fine but can't seek in OpenCV (H.264 in .m2ts): let FFmpeg take those
        if self.frame_count > 2 and self.read(self.frame_count // 2) is None:
            self.cap.release()
            raise ValueError("OpenCV can't seek in this file")
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    def read(self, number):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, number)
        ok, frame = self.cap.read()
        return frame if ok else None

    def next(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def skip(self):
        return self.cap.grab()

    def full_frame(self, number):
        return self.read(number)

    def release(self):
        self.cap.release()


class FFmpegVideo:
    """Decodes through an FFmpeg pipe at preview size. Seeking restarts the pipe at that time."""
    PREVIEW_WIDTH = 1280

    def __init__(self, path, info):
        self.path = path
        self.ffmpeg, _ = ffmpeg_tools()
        if not self.ffmpeg or not info.get("width") or not info.get("height"):
            raise ValueError("FFmpeg can't decode this file")
        self.fps = info.get("fps") or 25.0
        frames = info.get("frames")
        if not frames and info.get("duration"):
            frames = int(info["duration"] * self.fps)   # whole frames only: the last partial one has no picture
        self.frame_count = max(1, int(frames or 1))
        width, height = int(info["width"]), int(info["height"])
        scale = min(1.0, self.PREVIEW_WIDTH / width)
        self.width = max(2, int(width * scale) // 2 * 2)
        self.height = max(2, int(height * scale) // 2 * 2)
        self.process = None
        if self.read(0) is None:
            self.release()
            raise ValueError("FFmpeg can't decode this file")

    def _start(self, number, decode_from_start=False):
        self.release()
        seek = ["-ss", "{:.4f}".format(number / self.fps)] if number else []
        command = [self.ffmpeg, "-v", "error"]
        if not decode_from_start:
            command += seek                        # fast: jump near the frame
        command += ["-i", self.path]
        if decode_from_start:
            command += seek                        # slow but sure: decode up to the frame
        command += ["-an", "-sn", "-vf", "scale={}:{}".format(self.width, self.height),
                    "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)

    def read(self, number):
        self._start(number)
        frame = self.next()
        if frame is None and number:
            # the fast seek found no keyframe to start from (some transport streams)
            self._start(number, decode_from_start=True)
            frame = self.next()
        return frame

    def next(self):
        if self.process is None:
            return None
        size = self.width * self.height * 3
        data = self.process.stdout.read(size)
        if len(data) < size:
            return None
        return np.frombuffer(data, np.uint8).reshape(self.height, self.width, 3)

    def skip(self):
        return self.next() is not None

    def full_frame(self, number):
        image = ffmpeg_grab(self.path, number / self.fps)
        return None if image is None else cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)

    def release(self):
        if self.process is not None:
            try:
                self.process.kill()
                self.process.stdout.close()
                self.process.wait(timeout=2)
            except Exception:
                pass
            self.process = None


def open_video(path, info=None):
    """The best available decoder for a clip, or None."""
    try:
        return OpenCVVideo(path, info)
    except Exception:
        pass
    info = info or probe(path)
    if info.get("kind") == "video":
        try:
            return FFmpegVideo(path, info)
        except Exception:
            pass
    return None


def video_thumbnail(path, max_size):
    """A frame from about a second in (first frames are often black or a slate)."""
    image = None
    capture = cv2.VideoCapture(path)
    try:
        if capture.isOpened():
            fps = capture.get(cv2.CAP_PROP_FPS) or 25
            starts = [int(fps), 0] if capture.get(cv2.CAP_PROP_FRAME_COUNT) > fps * 2 else [0]
            for start in starts:                 # fall back to the first frame if seeking fails
                capture.set(cv2.CAP_PROP_POS_FRAMES, start)
                ok, frame = capture.read()
                if ok:
                    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    break
    finally:
        capture.release()
    if image is None:
        seconds = 1.0 if (probe(path).get("duration") or 0) > 2 else 0.0
        for start in dict.fromkeys([seconds, 0.0]):
            image = ffmpeg_grab(path, start, max_width=max_size[0] * 2)
            if image is not None:
                break
    if image is not None:
        image.thumbnail(max_size)
    return image


def describe_audio(info):
    parts = []
    if info.get("duration"):
        parts.append(format_time(info["duration"]))
    if info.get("sample_rate"):
        parts.append("{:g} kHz".format(info["sample_rate"] / 1000))
    if info.get("bits"):
        parts.append("{}-bit".format(int(info["bits"])))
    channels = info.get("channels")
    if channels:
        parts.append({1: "Mono", 2: "Stereo"}.get(channels, "{} ch".format(channels)))
    if info.get("audio_codec"):
        parts.append(str(info["audio_codec"]).upper().replace("PCM_", "PCM "))
    return "  ·  ".join(parts)


def format_time(seconds):
    seconds = max(0, int(round(seconds or 0)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return "{}:{:02d}:{:02d}".format(hours, minutes, secs) if hours else "{}:{:02d}".format(minutes, secs)


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Media Browser"
        self.description = "Browse and preview any video, audio or image file in a folder."
        self.category = "Media"
        self.icon = "film"
        self.window = None
        self.media_browser = None

    def create_window(self, parent):
        self.window = self.create_tool_window(parent, self.name, 1240, 780)
        self.window.minsize(900, 600)
        self.media_browser = MediaBrowser(self.window, tool=self)
        self.media_browser.pack(fill="both", expand=True)
        return self.window

    def close_window(self):
        if self.media_browser:
            self.media_browser.close()


class MediaBrowser(ctk.CTkFrame):
    PROTECTED_FOLDER_NAMES = {
        "$recycle.bin",
        "system volume information",
        "$windows.~bt",
        "$windows.~ws",
    }
    THUMB_SIZE = (120, 80)
    PLAYABLE_DIRECTLY = {".wav", ".wave", ".bwf", ".mp3", ".ogg", ".oga", ".flac", ".opus", ".aif", ".aiff"}

    def __init__(self, parent, tool=None):
        super().__init__(parent, fg_color="transparent")
        self.tool = tool

        # Configure grid weights
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Initialize variables
        self.current_folder = ""
        self.media_extensions = MEDIA_EXTENSIONS

        # Player state
        self.video = None                 # OpenCVVideo / FFmpegVideo
        self.current_frame = 0
        self.total_frames = 0
        self.fps = 30
        self.is_playing = False
        self.current_video_path = None    # the selected file, whatever its kind
        self.current_media_type = None    # "video" | "audio" | "image" | None
        self.last_frame = None            # BGR frame shown, for redraws on resize
        self.still_image = None           # PIL image shown for stills, audio and "can't preview"
        self.audio_path = None            # what pygame plays (the file or a WAV made from it)
        self.audio_position = 0.0
        self.audio_length = 0.0
        self.audio_start_offset = 0.0
        self.audio_update_timer = None
        self.audio_temp_dir = None
        self.pending_seek = None
        self.play_clock = None             # (start time, start frame, speed) while a video plays
        self.video_sound = None            # playable audio made from the current video
        self.audio_cache = {}              # file -> preview audio already made this session
        self.peaks = {}                    # file -> waveform
        self.wave_box = None               # where the waveform sits on the audio card (card pixels)
        self.view_transform = (0, 0, 1.0)  # card pixels -> canvas pixels
        self.timeline_images = []
        self.clip_buttons = {}
        self.kinds = {}
        self.folder_tree = None
        self.closed = False
        self.playback_timer = None
        self.scan_generation = 0
        self.results = queue.Queue()     # from background threads; only the UI thread touches Tk
        self.placeholders = {}

        self.setup_ui()
        self.after(60, self._poll_results)

    def setup_ui(self):
        # --- Header + toolbar -------------------------------------------------
        controls_frame = ctk.CTkFrame(self, fg_color="transparent")
        controls_frame.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        controls_frame.grid_columnconfigure(1, weight=1)

        if self.tool is not None:
            theme.icon_tile(controls_frame, theme.tool_icon(self.tool), theme.tool_category(self.tool),
                            size=44, icon_size=22).grid(row=0, column=0, rowspan=2, padx=(0, 14))
        ctk.CTkLabel(controls_frame, text="Media Browser", font=theme.font("display"), anchor="w",
                     height=26).grid(row=0, column=1, sticky="w")
        # Current folder label (doubles as the header subtitle)
        self.folder_label = theme.muted(controls_frame, "No folder selected", role="body", height=20)
        self.folder_label.grid(row=1, column=1, sticky="w")

        toolbar = ctk.CTkFrame(controls_frame, fg_color="transparent")
        toolbar.grid(row=0, column=2, rowspan=2, sticky="e")

        search_box = ctk.CTkFrame(toolbar, fg_color=C["field"], border_width=1, border_color=C["border_strong"],
                                  corner_radius=8, width=240, height=34)
        search_box.pack(side="left")
        search_box.pack_propagate(False)
        ctk.CTkLabel(search_box, text="", image=theme.icon("search", C["text_subtle"], 15), width=18).pack(
            side="left", padx=(10, 2))
        self.search_var = tk.StringVar()
        self.search_entry = ctk.CTkEntry(
            search_box, textvariable=self.search_var, placeholder_text="Search filenames...",
            border_width=0, fg_color="transparent", height=28
        )
        self.search_entry.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=2)
        self.search_entry.bind("<Return>", lambda event: self.search_media())
        theme.Button(toolbar, "Search", kind="secondary", width=74, command=self.search_media).pack(
            side="left", padx=(6, 0))
        theme.Button(toolbar, "Clear", kind="ghost", width=60, command=self.clear_search).pack(side="left")

        theme.Button(toolbar, "Refresh", kind="secondary", width=92,
                     image=theme.icon("refresh-cw", C["text"], 15), compound="left",
                     command=self.refresh_media).pack(side="left", padx=(10, 0))
        theme.Button(toolbar, "Browse Folder…", kind="primary", width=140,
                     command=self.browse_folder).pack(side="left", padx=(8, 0))

        # --- Main content: folder tree | viewer + metadata --------------------
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.grid(row=1, column=0, sticky="nsew", padx=24)
        content_frame.grid_columnconfigure(0, weight=1, minsize=210)
        content_frame.grid_columnconfigure(1, weight=4)
        content_frame.grid_rowconfigure(0, weight=1)

        # Left panel - Folder tree (subfolders load when you open a folder, so big drives stay fast)
        left_card = theme.Card(content_frame, title="Folders", padding=12)
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left_panel = left_card.body
        left_panel.grid_rowconfigure(0, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)
        theme.style_ttk(self)
        self.folder_tree = ttk.Treeview(left_panel, show="tree")
        self.folder_tree.grid(row=0, column=0, sticky="nsew")
        self.folder_tree.bind("<<TreeviewSelect>>", self.on_folder_select)
        self.folder_tree.bind("<<TreeviewOpen>>", self.on_folder_open)
        theme.add_scrollbar(self.folder_tree, left_panel)

        # Right panel - Viewer and metadata
        right_card = theme.Card(content_frame, padding=12)
        right_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right_panel = right_card.body
        right_panel.grid_rowconfigure(0, weight=1)
        right_panel.grid_columnconfigure(0, weight=3)
        right_panel.grid_columnconfigure(1, weight=2)

        # Viewer (stays black: it's a viewer). Double-click opens the file in its usual app.
        self.video_canvas = tk.Canvas(right_panel, bg='black', highlightthickness=0)
        self.video_canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.video_canvas.bind("<Configure>", lambda e: self._redraw_viewer())
        self.video_canvas.bind("<Double-Button-1>", lambda e: self.open_current_in_app())
        self.video_canvas.bind("<Button-1>", self.on_viewer_click)       # click the waveform to seek
        self.video_canvas.bind("<B1-Motion>", self.on_viewer_click)

        # Metadata display
        self.metadata_text = ctk.CTkTextbox(right_panel, width=300, font=theme.font("mono"), wrap="word")
        self.metadata_text.grid(row=0, column=1, rowspan=2, sticky="nsew")
        self.metadata_text.insert("1.0", "Metadata will appear here.")

        # Transport controls
        video_controls = ctk.CTkFrame(right_panel, fg_color="transparent")
        video_controls.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=(10, 0))
        video_controls.grid_columnconfigure(5, weight=1)

        self.play_pause_btn = theme.Button(video_controls, "▶", kind="primary", width=42,
                                           command=self.toggle_play_pause)
        self.play_pause_btn.grid(row=0, column=0, padx=(0, 4))

        self.stop_btn = theme.Button(video_controls, "■", kind="secondary", width=40, command=self.stop_playback)
        self.stop_btn.grid(row=0, column=1, padx=2)

        self.prev_frame_btn = theme.Button(video_controls, "◀|", kind="secondary", width=40,
                                           command=self.previous_frame)
        self.prev_frame_btn.grid(row=0, column=2, padx=2)

        self.next_frame_btn = theme.Button(video_controls, "|▶", kind="secondary", width=40,
                                           command=self.next_frame)
        self.next_frame_btn.grid(row=0, column=3, padx=2)

        self.frame_label = ctk.CTkLabel(video_controls, text="Frame: 0/0", font=theme.font("mono"),
                                        text_color=C["text_muted"])
        self.frame_label.grid(row=0, column=4, padx=(10, 6))

        self.timeline_slider = ctk.CTkSlider(video_controls, from_=0, to=100, height=16,
                                             command=self.on_timeline_change)
        self.timeline_slider.grid(row=0, column=5, padx=6, sticky="ew")
        self.timeline_slider.set(0)

        self.speed_label = theme.muted(video_controls, "Speed", role="body")
        self.speed_label.grid(row=0, column=6, padx=(10, 6))

        self.speed_var = tk.StringVar(value="1x")
        self.speed_var.trace_add("write", lambda *a: self._restart_clock())
        self.speed_menu = ctk.CTkOptionMenu(video_controls, variable=self.speed_var,
                                            values=["0.25x", "0.5x", "1x", "2x", "4x"],
                                            width=80, height=32)
        self.speed_menu.grid(row=0, column=7)

        # Additional controls
        extra_controls = ctk.CTkFrame(right_panel, fg_color="transparent")
        extra_controls.grid(row=2, column=0, sticky="ew", padx=(0, 12), pady=(8, 0))

        jump_label = theme.muted(extra_controls, "Jump to frame", role="body")
        jump_label.grid(row=0, column=0, padx=(0, 6))

        self.jump_entry = ctk.CTkEntry(extra_controls, width=80, height=32)
        self.jump_entry.grid(row=0, column=1)
        self.jump_entry.bind('<Return>', lambda e: self.jump_to_frame())

        jump_btn = theme.Button(extra_controls, "Go", kind="secondary", width=44, height=32,
                                command=self.jump_to_frame)
        jump_btn.grid(row=0, column=2, padx=(6, 0))

        step_label = theme.muted(extra_controls, "Step size", role="body")
        step_label.grid(row=0, column=3, padx=(20, 6))

        self.step_var = tk.IntVar(value=1)
        self.step_spin = ctk.CTkEntry(extra_controls, width=60, height=32, textvariable=self.step_var)
        self.step_spin.grid(row=0, column=4)

        self.export_btn = theme.Button(extra_controls, "Export Frame…", kind="secondary", width=120, height=32,
                                       command=self.export_frame)
        self.export_btn.grid(row=0, column=5, padx=(20, 0))

        reveal_text = {"Windows": "Show in Explorer", "Darwin": "Show in Finder"}.get(platform.system(),
                                                                                     "Show in Folder")
        # File actions, under the metadata
        file_actions = ctk.CTkFrame(right_panel, fg_color="transparent")
        file_actions.grid(row=2, column=1, sticky="ew", pady=(8, 0))
        file_actions.grid_columnconfigure((0, 1), weight=1, uniform="actions")
        self.open_btn = theme.Button(file_actions, "Open in App", kind="secondary", height=32,
                                     image=theme.icon("external-link", C["text"], 15), compound="left",
                                     command=self.open_current_in_app)
        self.open_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.reveal_btn = theme.Button(file_actions, reveal_text, kind="secondary", height=32,
                                       image=theme.icon("folder-open", C["text"], 15), compound="left",
                                       command=self.reveal_current)
        self.reveal_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # --- Clips strip --------------------------------------------------------
        clips_card = theme.Card(self, title="Clips", padding=12)
        clips_card.grid(row=2, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.preview_timeline = ctk.CTkScrollableFrame(
            clips_card.body, orientation="horizontal", height=112, fg_color="transparent"
        )
        self.preview_timeline.pack(fill="x")

        # Status line
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=3, column=0, sticky="ew", padx=24, pady=(8, 12))

        self.info_label = theme.muted(info_frame, "Select a folder to browse media files", height=20)
        self.info_label.grid(row=0, column=0, sticky="w")

        self.set_controls_for(None)

    # ================================================================== folders and listing
    def browse_folder(self):
        """Open folder dialog and set current folder"""
        folder = filedialog.askdirectory(title="Select Media Folder")
        if folder:
            self.current_folder = folder
            self.folder_label.configure(text=f"Folder: {os.path.basename(folder)}")
            self.populate_folder_tree()
            self.refresh_media()

    def refresh_media(self):
        """List the media in the current folder."""
        if not self.current_folder:
            return
        try:
            known, unknown = self.collect_media_files(self.current_folder, split=True)
            self.show_files(known, unknown, "Found {} media file{}")
        except Exception as e:
            messagebox.showerror("Error", f"Error reading folder: {str(e)}")

    def search_media(self):
        """Search media filenames recursively below the selected folder."""
        if not self.current_folder:
            self.info_label.configure(text="Select a folder before searching")
            return

        query = self.search_var.get().strip().lower()
        if not query:
            self.refresh_media()
            return

        known, unknown = [], []
        for root, dirs, files in os.walk(self.current_folder, onerror=self.handle_walk_error):
            dirs[:] = [folder for folder in dirs
                       if folder.lower() not in self.PROTECTED_FOLDER_NAMES and not is_hidden(folder)]
            for filename in files:
                if query in filename.lower() and not is_hidden(filename):
                    path = os.path.join(root, filename)
                    if self.is_media_file(filename):
                        known.append(path)
                    elif self._worth_probing(filename):
                        unknown.append(path)
        known.sort(key=lambda path: path.lower())
        self.show_files(known, unknown, "Search: {} match{}", plural="es")

    def clear_search(self):
        self.search_var.set("")
        self.refresh_media()

    def handle_walk_error(self, error):
        print(f"Skipping inaccessible folder {error.filename}: {error}")

    def _worth_probing(self, filename):
        return os.path.splitext(filename)[1].lower() not in NOT_MEDIA_EXTENSIONS

    def collect_media_files(self, folder=None, split=False):
        """Media files in a folder, by extension. With split=True also returns the other files
        worth checking with ffprobe (unknown or missing extensions)."""
        known, unknown = [], []
        folder = folder or self.current_folder
        if not os.path.exists(folder):
            return (known, unknown) if split else known
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if not entry.is_file() or is_hidden(entry.name):   # follows links to files, like Finder
                            continue
                    except OSError:
                        continue
                    if self.is_media_file(entry.name):
                        known.append(entry.path)
                    elif self._worth_probing(entry.name):
                        unknown.append(entry.path)
        except OSError as error:
            print(f"Unable to read folder {folder}: {error}")
        key = lambda path: os.path.basename(path).lower()
        known.sort(key=key)
        unknown.sort(key=key)
        return (known, unknown) if split else known

    def populate_folder_tree(self):
        self.folder_tree.delete(*self.folder_tree.get_children())
        root_id = self.folder_tree.insert("", "end", text=os.path.basename(self.current_folder) or self.current_folder,
                                          values=(self.current_folder,), open=True)
        self.add_folder_tree_nodes(root_id, self.current_folder)
        self.folder_tree.selection_set(root_id)

    def add_folder_tree_nodes(self, parent_id, folder):
        """Add one level of subfolders. Deeper levels load when a folder is opened."""
        for path, name in self._subfolders(folder):
            node_id = self.folder_tree.insert(parent_id, "end", text=name, values=(path,))
            if self._has_subfolder(path):
                self.folder_tree.insert(node_id, "end", text="…", values=("",))   # makes it expandable

    def _subfolders(self, folder):
        try:
            with os.scandir(folder) as scanner:
                entries = sorted(scanner, key=lambda entry: entry.name.lower())
        except OSError as error:
            print(f"Skipping inaccessible folder {folder}: {error}")
            return []
        found = []
        for entry in entries:
            if entry.name.lower() in self.PROTECTED_FOLDER_NAMES or is_hidden(entry.name):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    found.append((entry.path, entry.name))
            except OSError as error:
                print(f"Skipping inaccessible folder {entry.path}: {error}")
        return found

    def _has_subfolder(self, folder):
        try:
            with os.scandir(folder) as scanner:
                for entry in scanner:
                    try:
                        if entry.is_dir(follow_symlinks=False) and not is_hidden(entry.name):
                            return True
                    except OSError:
                        continue
        except OSError:
            pass
        return False

    def on_folder_open(self, event):
        node = self.folder_tree.focus()
        children = self.folder_tree.get_children(node)
        if len(children) == 1 and self.folder_tree.item(children[0], "values") in (("",), ""):
            self.folder_tree.delete(children[0])
            self.add_folder_tree_nodes(node, self.folder_tree.item(node, "values")[0])

    def on_folder_select(self, event):
        selection = self.folder_tree.selection()
        if not selection:
            return
        values = self.folder_tree.item(selection[0], "values")
        if not values or not values[0]:
            return
        folder = values[0]
        self.current_folder = folder
        self.folder_label.configure(text=f"Folder: {os.path.basename(folder)}")
        self.refresh_media()

    # ================================================================== clips strip
    def show_files(self, known, unknown, message, plural="s"):
        """Show clips for known media now; check the rest with ffprobe in the background."""
        self.scan_generation += 1
        self.kinds = {path: extension_kind(path) for path in known}
        self.populate_preview_timeline(known)
        self._list_message = (message, plural)
        self._update_count()
        unknown = unknown[:MAX_PROBED_FILES]
        generation = self.scan_generation
        threading.Thread(target=self._scan_worker, args=(generation, list(known), unknown), daemon=True).start()

    def _update_count(self):
        message, plural = getattr(self, "_list_message", ("Found {} media file{}", "s"))
        count = len(self.clip_buttons)
        if count or "Search" in message:
            self.info_label.configure(text=message.format(count, "" if count == 1 else plural))
        else:
            self.info_label.configure(text="No media files found in selected folder")

    def populate_preview_timeline(self, media_files):
        for widget in self.preview_timeline.winfo_children():
            widget.destroy()
        self.clip_buttons = {}
        self.timeline_images = []
        for file_path in media_files:
            self._add_clip_button(file_path)

    def _add_clip_button(self, file_path):
        kind = self.kinds.get(file_path)
        image = self._placeholder("audio" if kind == "audio" else "loading")
        button = ctk.CTkButton(
            self.preview_timeline,
            text=os.path.basename(file_path),
            image=image,
            compound="top",
            width=130,
            height=105,
            fg_color=C["surface_2"],
            hover_color=C["hover"],
            text_color=C["text"],
            font=theme.font("caption"),
            command=lambda path=file_path: self.select_media_file(path),
        )
        button.pack(side="left", padx=4, pady=4)
        button.bind("<Double-Button-1>", lambda e, path=file_path: self.open_media_file(path))
        self.clip_buttons[file_path] = button
        self.timeline_images.append(image)

    def _scan_worker(self, generation, known, unknown):
        """Background: thumbnails for the listed clips, then ffprobe for files with odd extensions."""
        def current():
            return generation == self.scan_generation and not self.closed

        for path in known:
            if not current():
                return
            kind = extension_kind(path)
            if kind != "audio":
                self.results.put(("thumb", generation, path, self.create_thumbnail_image(path, kind)))
        for path in unknown:
            if not current():
                return
            kind = probe(path).get("kind")
            if kind:
                self.results.put(("add", generation, path, kind))
                if kind != "audio":
                    self.results.put(("thumb", generation, path, self.create_thumbnail_image(path, kind)))

    def _poll_results(self):
        """UI thread: apply what the background threads found."""
        if self.closed:
            return
        try:
            while True:
                message = self.results.get_nowait()
                kind = message[0]
                if kind in ("add", "thumb") and message[1] != self.scan_generation:
                    continue
                if kind == "add":
                    _, _, path, media_kind = message
                    if path not in self.clip_buttons:
                        self.kinds[path] = media_kind
                        self._add_clip_button(path)
                        self._update_count()
                elif kind == "thumb":
                    _, _, path, image = message
                    button = self.clip_buttons.get(path)
                    if button is not None and button.winfo_exists():
                        ctk_image = (ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                                     if image is not None else self._placeholder("unsupported"))
                        self.timeline_images.append(ctk_image)
                        button.configure(image=ctk_image)
                elif kind == "audio_ready":
                    _, path, wav_path, purpose = message
                    self.audio_cache[path] = wav_path
                    if path == self.current_video_path and purpose == "audio":
                        self._audio_file_ready(wav_path)
                    elif path == self.current_video_path and purpose == "video":
                        self._video_sound_ready(wav_path)
                elif kind == "audio_failed":
                    _, path, error, purpose = message
                    if path == self.current_video_path and purpose == "audio":
                        self.info_label.configure(text=f"Can't play {os.path.basename(path)}: {error}")
                elif kind == "peaks":
                    _, path, peaks = message
                    self.peaks[path] = peaks
                    if path == self.current_video_path and self.current_media_type == "audio":
                        self._redraw_viewer()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(60, self._poll_results)

    def create_thumbnail_image(self, file_path, kind=None):
        """PIL thumbnail for a clip or still (None if it can't be decoded). Safe off the UI thread."""
        kind = kind or extension_kind(file_path) or probe(file_path).get("kind")
        try:
            if kind == "video":
                image = video_thumbnail(file_path, self.THUMB_SIZE)
            elif kind == "image":
                image = load_still(file_path, self.THUMB_SIZE)
            else:
                return None
        except Exception:
            return None
        if image is None:
            # e.g. a .mov that only holds audio: check what's really inside
            if probe(file_path).get("kind") == "audio":
                return self._audio_thumbnail_pil()
        return image

    def create_thumbnail(self, file_path):
        image = self.create_thumbnail_image(file_path)
        if image is None:
            return self._placeholder("unsupported")
        return ctk.CTkImage(light_image=image, dark_image=image, size=image.size)

    def _audio_thumbnail_pil(self):
        return self._tile_pil("audio-lines", C["accent"][1], "#1C1E2B")

    def _tile_pil(self, icon_name, fg, bg, size=(240, 160), icon_size=64):
        tile = Image.new("RGB", size, bg)
        mask = self._icon_mask(icon_name, icon_size)
        if mask is not None:
            glyph = Image.new("RGB", mask.size, fg)
            tile.paste(glyph, ((size[0] - icon_size) // 2, (size[1] - icon_size) // 2), mask)
        return tile

    def _icon_mask(self, icon_name, size):
        path = os.path.join(theme.ICON_DIR, icon_name + ".png")
        try:
            return Image.open(path).convert("RGBA").getchannel("A").resize((size, size), Image.LANCZOS)
        except OSError:
            return None

    def _placeholder(self, which):
        if which not in self.placeholders:
            if which == "audio":
                light = self._tile_pil("audio-lines", C["accent"][0], "#E8EBFA")
                dark = self._audio_thumbnail_pil()
            elif which == "unsupported":
                light = self._tile_pil("image-off", C["text_subtle"][0], C["surface_2"][0])
                dark = self._tile_pil("image-off", C["text_subtle"][1], C["surface_2"][1])
            else:
                light = Image.new("RGB", (240, 160), C["border"][0])
                dark = Image.new("RGB", (240, 160), C["border_strong"][1])
            self.placeholders[which] = ctk.CTkImage(light_image=light, dark_image=dark, size=self.THUMB_SIZE)
        return self.placeholders[which]

    # ================================================================== selecting a file
    def select_media_file(self, file_path):
        """Preview any media file in the viewer."""
        self.stop_everything()
        self.current_video_path = file_path
        self.show_metadata(file_path)
        kind = self.kinds.get(file_path) or extension_kind(file_path) or probe(file_path).get("kind")
        if kind == "video":
            if not self.load_video(file_path):
                # not decodable as video: maybe it only holds audio (an audio-only .mov or .mxf)
                if probe(file_path).get("kind") == "audio":
                    self.load_audio(file_path)
                else:
                    self.show_unsupported(file_path)
        elif kind == "audio":
            self.load_audio(file_path)
        elif kind == "image":
            self.load_image(file_path)
        else:
            self.show_unsupported(file_path)

    def stop_everything(self):
        self.is_playing = False
        self.play_clock = None
        self.video_sound = None
        self.stop_playback_timer()
        self.stop_audio_timer()
        self.play_pause_btn.configure(text="▶")
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
        except Exception:
            pass
        if self.video:
            self.video.release()
            self.video = None
        self.audio_path = None
        self.last_frame = None
        self.still_image = None

    def schedule_ui(self, callback, *args):
        if not self.closed and self.winfo_exists():
            self.after(0, self.run_ui_callback, callback, args)

    def run_ui_callback(self, callback, args):
        if not self.closed and self.winfo_exists():
            callback(*args)

    def close(self):
        self.closed = True
        self.is_playing = False
        self.stop_playback_timer()
        self.stop_audio_timer()
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.quit()
        except Exception:
            pass
        if self.video:
            self.video.release()
            self.video = None
        if self.audio_temp_dir:
            shutil.rmtree(self.audio_temp_dir, ignore_errors=True)
            self.audio_temp_dir = None

    def is_media_file(self, filename):
        """Media by extension (files with other extensions are checked with ffprobe)."""
        return os.path.splitext(filename.lower())[1] in self.media_extensions

    def is_video_file(self, filename):
        return extension_kind(filename) == "video"

    def is_audio_file(self, filename):
        return extension_kind(filename) == "audio"

    def show_metadata(self, file_path):
        try:
            from pymediainfo import MediaInfo
        except ImportError:
            info = probe(file_path)
            lines = ["{}: {}".format(key, value) for key, value in info.items() if value not in (None, "")]
            self.metadata_text.delete("1.0", "end")
            self.metadata_text.insert("1.0", "\n".join(lines) or "Install pymediainfo for full metadata.")
            return
        try:
            media_info = MediaInfo.parse(file_path)
            info_lines = []
            for track in media_info.tracks:
                info_lines.append(f"[{track.track_type}]\n")
                for key, value in track.to_data().items():
                    info_lines.append(f"{key}: {value}")
                info_lines.append("")
            self.metadata_text.delete("1.0", "end")
            self.metadata_text.insert("1.0", "\n".join(info_lines))
        except Exception as e:
            self.metadata_text.delete("1.0", "end")
            self.metadata_text.insert("1.0", f"Error reading metadata: {e}")

    # ================================================================== viewer
    def _canvas_size(self):
        return self.video_canvas.winfo_width(), self.video_canvas.winfo_height()

    def _show_pil(self, image):
        """Fit a PIL image into the viewer."""
        width, height = self._canvas_size()
        if width <= 1 or height <= 1 or image is None:
            return
        scale = min(width / image.width, height / image.height)
        size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        photo = ImageTk.PhotoImage(image.resize(size, Image.LANCZOS if scale < 1 else Image.BILINEAR))
        self.video_canvas.delete("all")
        self.canvas_image_id = self.video_canvas.create_image(width // 2, height // 2, anchor="center", image=photo)
        self.video_canvas.image = photo
        self.view_transform = ((width - size[0]) / 2, (height - size[1]) / 2, scale)

    def _redraw_viewer(self):
        if self.still_image is not None:
            self._show_pil(self.still_image() if callable(self.still_image) else self.still_image)
            if self.current_media_type == "audio":
                self._move_playhead()
        elif self.last_frame is not None:
            self._render_frame(self.last_frame, self.current_frame, remember=False)

    def _card(self, icon_name, title, detail, icon_color):
        """A dark card for the viewer: large icon, file name, one line of detail."""
        width, height = self._canvas_size()
        width, height = max(width, 320), max(height, 200)
        card = Image.new("RGB", (width, height), "#0B0B0C")
        draw = ImageDraw.Draw(card)
        icon_size = max(48, min(160, height // 3))
        top = (height - icon_size - 70) // 2
        mask = self._icon_mask(icon_name, icon_size)
        if mask is not None:
            card.paste(Image.new("RGB", mask.size, icon_color), ((width - icon_size) // 2, top), mask)
        title_font, detail_font = self._fonts(max(15, icon_size // 7), max(12, icon_size // 10))
        y = top + icon_size + 22
        for text, font, fill in ((title, title_font, "#EDEDED"), (detail, detail_font, "#8A8A8A")):
            text = self._fit(draw, text, font, width - 40)
            text_width = draw.textlength(text, font=font)
            draw.text(((width - text_width) / 2, y), text, font=font, fill=fill)
            y += font.size + 12
        return card

    def _fit(self, draw, text, font, max_width):
        if draw.textlength(text, font=font) <= max_width:
            return text
        while text and draw.textlength(text + "…", font=font) > max_width:
            text = text[:-1]
        return text + "…"

    def _fonts(self, title_size, detail_size):
        key = (title_size, detail_size)
        cache = getattr(self, "_font_cache", {})
        if key not in cache:
            fonts = []
            for size in key:
                font = None
                for name in ("segoeui.ttf", "Arial.ttf", "arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf",
                             "LiberationSans-Regular.ttf"):
                    try:
                        font = ImageFont.truetype(name, size)
                        break
                    except OSError:
                        continue
                fonts.append(font or ImageFont.load_default(size))
            cache[key] = tuple(fonts)
            self._font_cache = cache
        return cache[key]

    def show_unsupported(self, file_path):
        self.current_media_type = None
        name = os.path.basename(file_path)
        self.still_image = lambda: self._card("image-off", name, "No preview for this file. Double-click to open it in its app.",
                                              "#6F6F6F")
        self._redraw_viewer()
        self.set_controls_for(None, file_selected=True)
        self.frame_label.configure(text="—")
        self.info_label.configure(text=f"Can't preview {name}")

    # ------------------------------------------------------------------ images
    def load_image(self, file_path):
        image = load_still(file_path)
        if image is None:
            self.show_unsupported(file_path)
            return
        self.current_media_type = "image"
        self.still_image = image
        self._redraw_viewer()
        self.set_controls_for("image")
        self.frame_label.configure(text=f"{image.width} × {image.height}")
        self.info_label.configure(text=f"Loaded: {os.path.basename(file_path)} ({image.width} × {image.height})")

    # ------------------------------------------------------------------ video
    def load_video(self, file_path):
        """Load a clip into the viewer. Returns False if nothing can decode it."""
        self.current_media_type = "video"
        self.video = open_video(file_path)
        if self.video is None:
            return False
        self.total_frames = self.video.frame_count
        self.fps = self.video.fps
        self.current_frame = 0
        self.timeline_slider.configure(to=max(1, self.total_frames - 1))
        self.timeline_slider.set(0)
        self.frame_label.configure(text=f"Frame: 0/{self.total_frames - 1}")
        decoder = "" if isinstance(self.video, OpenCVVideo) else ", via FFmpeg"
        self.info_label.configure(
            text=f"Loaded: {os.path.basename(file_path)} ({self.total_frames} frames @ {self.fps:.2f} fps{decoder})")
        self.set_controls_for("video")
        self.display_frame(0)
        self._prepare_video_sound(file_path)
        return True

    # ------------------------------------------------------------------ video sound
    def _prepare_video_sound(self, file_path):
        """Make the clip's audio playable in the background; playback follows it once it's ready."""
        info = probe(file_path)
        if not info.get("audio_codec"):
            return
        try:
            pygame = self._get_pygame()
            if not pygame.mixer.get_init():
                pygame.mixer.init()
        except Exception:
            return
        cached = self.audio_cache.get(file_path)
        if cached and os.path.exists(cached):
            self._video_sound_ready(cached)
            return
        ffmpeg, _ = ffmpeg_tools()
        if ffmpeg:
            self._start_conversion(file_path, ffmpeg, info, "video")

    def _video_sound_ready(self, playable_path):
        try:
            self._get_pygame().mixer.music.load(playable_path)
        except Exception:
            return
        self.video_sound = playable_path
        if self.is_playing and self.play_clock:
            self._start_video_sound()             # joined mid-playback: pick up where the picture is

    def _sound_allowed(self):
        return self.video_sound is not None and self.speed_var.get() == "1x"

    def _start_video_sound(self):
        if not self._sound_allowed():
            return
        try:
            self._get_pygame().mixer.music.play(start=self.current_frame / max(self.fps, 1.0))
        except Exception:
            pass

    def _stop_video_sound(self):
        if self.video_sound is None:
            return
        try:
            self._get_pygame().mixer.music.stop()
        except Exception:
            pass

    def display_frame(self, frame_number):
        """Display a specific frame."""
        if self.closed or not self.winfo_exists() or not self.video:
            return
        try:
            frame = self.video.read(frame_number)
            if frame is not None:
                self._render_frame(frame, frame_number)
        except Exception as e:
            print(f"Error displaying frame: {e}")

    def _render_frame(self, frame, frame_number, remember=True):
        """Render a decoded BGR frame to the viewer."""
        if self.closed or not self.winfo_exists() or frame is None:
            return
        if remember:
            self.last_frame = frame
            self.still_image = None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        canvas_width, canvas_height = self._canvas_size()

        if canvas_width > 1 and canvas_height > 1:
            height, width = rgb.shape[:2]
            scale = min(canvas_width / width, canvas_height / height)
            new_width = max(1, int(width * scale))
            new_height = max(1, int(height * scale))
            rgb = cv2.resize(rgb, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
            photo = ImageTk.PhotoImage(image=Image.fromarray(rgb))
            self.video_canvas.delete("all")
            self.canvas_image_id = self.video_canvas.create_image(
                canvas_width // 2, canvas_height // 2, anchor="center", image=photo)
            self.video_canvas.image = photo

        self.current_frame = frame_number
        self.timeline_slider.set(frame_number)
        self.frame_label.configure(text=f"Frame: {frame_number}/{self.total_frames - 1}")

    # ------------------------------------------------------------------ audio
    def _get_pygame(self):
        try:
            import pygame
            return pygame
        except ImportError as exc:
            raise RuntimeError("pygame is required for audio playback. Install it with pip install pygame.") from exc

    def load_audio(self, file_path):
        """Show the audio card and get the file ready to play (converting it with FFmpeg if needed)."""
        info = probe(file_path)
        self.current_media_type = "audio"
        self.current_video_path = file_path
        self.audio_path = None
        self.audio_position = 0.0
        self.audio_start_offset = 0.0
        self.audio_length = info.get("duration") or 0.0
        name = os.path.basename(file_path)
        detail = describe_audio(info) or "Audio"
        self.still_image = lambda: self._audio_card(name, detail, self.peaks.get(file_path))
        self._redraw_viewer()
        if file_path not in self.peaks:
            threading.Thread(target=lambda: self.results.put(("peaks", file_path, audio_peaks(file_path, info))),
                             daemon=True).start()
        self.timeline_slider.configure(to=max(1.0, self.audio_length))
        self.timeline_slider.set(0)
        self.frame_label.configure(text=f"Time: 0.00/{self.audio_length:.2f}s")
        self.set_controls_for("audio", ready=False)

        try:
            pygame = self._get_pygame()
            if not pygame.mixer.get_init():
                pygame.mixer.init()
        except Exception as e:
            self.info_label.configure(text=f"Loaded: {name} (can't play audio: {e})")
            return

        if os.path.splitext(file_path)[1].lower() in self.PLAYABLE_DIRECTLY:
            try:
                pygame.mixer.music.load(file_path)
                self._audio_file_ready(file_path)
                return
            except Exception:
                pass
        # Anything else (AAC, M4A, WMA, audio in a .mov or .mxf...): make a file pygame can play
        cached = self.audio_cache.get(file_path)
        if cached and os.path.exists(cached):
            self._audio_file_ready(cached)
            return
        ffmpeg, _ = ffmpeg_tools()
        if not ffmpeg:
            self.info_label.configure(text=f"Loaded: {name} (install FFmpeg to play this format)")
            return
        self.info_label.configure(text=f"Preparing {name} for playback…")
        self._start_conversion(file_path, ffmpeg, info, "audio")

    def _start_conversion(self, file_path, ffmpeg, info, purpose):
        if self.audio_temp_dir is None:
            self.audio_temp_dir = tempfile.mkdtemp(prefix="autoae_audio_")
        self._trim_audio_cache()
        threading.Thread(target=self._convert_audio, args=(file_path, ffmpeg, info, purpose), daemon=True).start()

    def _trim_audio_cache(self, keep=6):
        """Previews live in a temp folder for the session; keep only the last few."""
        for path in list(self.audio_cache)[:-keep]:
            playable = self.audio_cache.pop(path)
            if playable not in (self.audio_path, self.video_sound):
                try:
                    os.remove(playable)
                except OSError:
                    pass

    def _convert_audio(self, file_path, ffmpeg, info, purpose):
        long_file = (info.get("duration") or 0) > LONG_AUDIO_SECONDS
        target = os.path.join(self.audio_temp_dir or tempfile.gettempdir(),
                              "preview_{}.{}".format(abs(hash(file_path)), "mp3" if long_file else "wav"))
        codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if long_file else ["-c:a", "pcm_s16le"]
        try:
            result = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", file_path] + audio_decode_args(info) +
                ["-vn", "-sn", "-ac", "2", "-ar", "48000"] + codec + [target],
                capture_output=True, timeout=1800, creationflags=NO_WINDOW)
            if result.returncode == 0 and os.path.exists(target):
                self.results.put(("audio_ready", file_path, target, purpose))
            else:
                error = (result.stderr or b"").decode(errors="replace").strip().splitlines()
                self.results.put(("audio_failed", file_path,
                                  error[-1] if error else "FFmpeg couldn't decode it", purpose))
        except Exception as e:
            self.results.put(("audio_failed", file_path, str(e), purpose))

    # ------------------------------------------------------------------ waveform
    def _audio_card(self, name, detail, peaks):
        """Audio card: icon, name, details, and the waveform across the bottom."""
        width, height = self._canvas_size()
        width, height = max(width, 320), max(height, 220)
        card = Image.new("RGB", (width, height), "#0B0B0C")
        draw = ImageDraw.Draw(card)
        icon_size = max(36, min(96, height // 5))
        top = int(height * 0.08)
        mask = self._icon_mask("audio-lines", icon_size)
        if mask is not None:
            card.paste(Image.new("RGB", mask.size, C["accent"][1]), ((width - icon_size) // 2, top), mask)
        title_font, detail_font = self._fonts(max(15, icon_size // 5), max(12, icon_size // 7))
        y = top + icon_size + 14
        for text, font, fill in ((name, title_font, "#EDEDED"), (detail, detail_font, "#8A8A8A")):
            text = self._fit(draw, text, font, width - 40)
            draw.text(((width - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)
            y += font.size + 10

        x0, x1 = 28, width - 28
        y0, y1 = max(y + 18, int(height * 0.52)), height - 24
        self.wave_box = (x0, y0, x1, y1)
        middle = (y0 + y1) / 2
        draw.line((x0, middle, x1, middle), fill="#2A2D3E")
        if peaks:
            half = (y1 - y0) / 2
            for x in range(x0, x1):
                peak = peaks[min(len(peaks) - 1, int((x - x0) / (x1 - x0) * len(peaks)))]
                reach = max(0.5, peak * half)
                draw.line((x, middle - reach, x, middle + reach), fill="#5B6BC9")
        else:
            draw.text((x0, y0), "Reading waveform…", font=detail_font, fill="#5A5A5A")
        return card

    def _wave_x(self, seconds):
        """Canvas x of a time on the waveform."""
        x0, _, x1, _ = self.wave_box
        offset_x, _, scale = self.view_transform
        fraction = 0 if not self.audio_length else max(0.0, min(1.0, seconds / self.audio_length))
        return offset_x + (x0 + fraction * (x1 - x0)) * scale

    def _move_playhead(self):
        if self.current_media_type != "audio" or not self.wave_box:
            return
        _, y0, _, y1 = self.wave_box
        offset_x, offset_y, scale = self.view_transform
        x = self._wave_x(self.audio_position)
        top, bottom = offset_y + (y0 - 6) * scale, offset_y + (y1 + 6) * scale
        if self.video_canvas.find_withtag("playhead"):
            self.video_canvas.coords("playhead", x, top, x, bottom)
        else:
            self.video_canvas.create_line(x, top, x, bottom, fill="#FFFFFF", width=2, tags="playhead")

    def on_viewer_click(self, event):
        """Click or drag on the waveform to move the playhead."""
        if self.current_media_type != "audio" or not self.wave_box or not self.audio_length:
            return
        x0, y0, x1, y1 = self.wave_box
        offset_x, offset_y, scale = self.view_transform
        left, right = offset_x + x0 * scale, offset_x + x1 * scale
        top, bottom = offset_y + (y0 - 10) * scale, offset_y + (y1 + 10) * scale
        if left <= event.x <= right and top <= event.y <= bottom:
            self.seek_audio((event.x - left) / (right - left) * self.audio_length)

    def _audio_file_ready(self, playable_path):
        pygame = self._get_pygame()
        try:
            pygame.mixer.music.load(playable_path)
        except Exception as e:
            self.info_label.configure(text=f"Can't play {os.path.basename(self.current_video_path)}: {e}")
            return
        self.audio_path = playable_path
        if not self.audio_length:
            try:
                self.audio_length = float(pygame.mixer.Sound(playable_path).get_length())
                self.timeline_slider.configure(to=max(1.0, self.audio_length))
            except Exception:
                self.audio_length = 0.0
        self.set_controls_for("audio")
        self.info_label.configure(
            text=f"Loaded: {os.path.basename(self.current_video_path)} ({self.audio_length:.2f}s)")
        self.frame_label.configure(text=f"Time: {self.audio_position:.2f}/{self.audio_length:.2f}s")

    def stop_playback_timer(self):
        """Cancel the queued timer callback used to advance playback."""
        if self.playback_timer is not None:
            try:
                self.after_cancel(self.playback_timer)
            except Exception:
                pass
            self.playback_timer = None

    def stop_audio_timer(self):
        """Cancel any queued audio position updates."""
        if self.audio_update_timer is not None:
            try:
                self.after_cancel(self.audio_update_timer)
            except Exception:
                pass
            self.audio_update_timer = None

    def _play_audio_from(self, seconds):
        pygame = self._get_pygame()
        self.audio_start_offset = max(0.0, seconds)
        pygame.mixer.music.play(start=self.audio_start_offset)

    def start_audio_playback(self):
        """Play the currently loaded audio file."""
        if not self.audio_path:
            return
        if self.audio_length and self.audio_position >= self.audio_length - 0.05:
            self.audio_position = 0.0
        try:
            self._play_audio_from(self.audio_position)
        except Exception as e:
            messagebox.showwarning("Audio playback failed", str(e))
            return
        self.is_playing = True
        self.play_pause_btn.configure(text="⏸")
        self._schedule_audio_updates()

    def pause_audio(self):
        """Pause the current audio track."""
        try:
            pygame = self._get_pygame()
            if pygame.mixer.get_init():
                pygame.mixer.music.pause()
        except Exception:
            pass
        self.is_playing = False
        self.stop_audio_timer()
        self.play_pause_btn.configure(text="▶")

    def seek_audio(self, target_seconds):
        """Move the playhead to a specific second."""
        if not self.audio_path:
            return
        self.audio_position = max(0.0, min(float(target_seconds), self.audio_length or float(target_seconds)))
        try:
            if self.is_playing:
                self._play_audio_from(self.audio_position)
            else:
                self._get_pygame().mixer.music.stop()
        except Exception as exc:
            messagebox.showwarning("Audio seek failed", str(exc))
        self.frame_label.configure(text=f"Time: {self.audio_position:.2f}/{self.audio_length:.2f}s")
        self.timeline_slider.set(self.audio_position)
        self._move_playhead()

    def _schedule_audio_updates(self):
        """Update the audio position slider while playing."""
        if not self.is_playing or not self.audio_path or self.closed:
            return
        self.audio_update_timer = self.after(200, self._update_audio_position)

    def _update_audio_position(self):
        """Refresh the playhead from pygame (get_pos counts from the last play() call)."""
        if not self.is_playing or not self.audio_path or self.closed:
            return
        try:
            pygame = self._get_pygame()
            if not pygame.mixer.get_init():
                return
            finished = not pygame.mixer.music.get_busy()
            position_ms = pygame.mixer.music.get_pos()
            if position_ms >= 0:
                self.audio_position = self.audio_start_offset + position_ms / 1000.0
                if self.audio_length:
                    self.audio_position = min(self.audio_length, self.audio_position)
                self.timeline_slider.set(self.audio_position)
                self.frame_label.configure(text=f"Time: {self.audio_position:.2f}/{self.audio_length:.2f}s")
                self._move_playhead()
            if finished:
                self.is_playing = False
                self.audio_position = 0.0
                self.play_pause_btn.configure(text="▶")
                self.stop_audio_timer()
                return
        except Exception:
            pass
        self._schedule_audio_updates()

    # ------------------------------------------------------------------ transport
    def start_playback(self):
        """Start video playback."""
        if not self.video:
            return
        if self.current_frame >= self.total_frames - 1:
            self.display_frame(0)
        self.video.read(self.current_frame)   # position the decoder; frames then come in order
        self.is_playing = True
        self.play_pause_btn.configure(text="⏸")
        self._restart_clock()

    def toggle_play_pause(self):
        """Toggle between play and pause on the current media type."""
        if self.current_media_type == "audio":
            if self.is_playing:
                self.pause_audio()
            else:
                self.start_audio_playback()
            return

        if self.is_playing:
            self.is_playing = False
            self.play_clock = None
            self.stop_playback_timer()
            self._stop_video_sound()
            self.play_pause_btn.configure(text="▶")
            return

        self.start_playback()

    def stop_playback(self):
        """Stop playback and return to the start."""
        self.is_playing = False
        self.play_clock = None
        self.stop_playback_timer()
        self.stop_audio_timer()
        self.play_pause_btn.configure(text="▶")

        if self.current_media_type == "audio":
            self.audio_position = 0.0
            try:
                pygame = self._get_pygame()
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
            except Exception:
                pass
            self.timeline_slider.set(0)
            self.frame_label.configure(text=f"Time: 0.00/{self.audio_length:.2f}s")
            self._move_playhead()
            return

        self._stop_video_sound()
        if self.video:
            self.display_frame(0)

    def _restart_clock(self):
        """(Re)start the playback clock from the current frame, e.g. after play or a speed change.
        Frames are shown by the clock, not one per timer tick, so the picture keeps pace with the sound."""
        if not self.is_playing or not self.video or self.current_media_type != "video":
            return
        self.stop_playback_timer()
        speeds = {"0.25x": 0.25, "0.5x": 0.5, "1x": 1.0, "2x": 2.0, "4x": 4.0}
        self.play_clock = (time.perf_counter(), self.current_frame, speeds.get(self.speed_var.get(), 1.0))
        self._stop_video_sound()
        self._start_video_sound()                 # only at 1x: other speeds play silent
        self._schedule_next_frame()

    def _schedule_next_frame(self):
        """Queue the next frame for when the clock says it's due."""
        if not self.is_playing or self.closed or not self.video or not self.play_clock:
            return
        started, first, speed = self.play_clock
        due = started + (self.current_frame + 1 - first) / (max(self.fps, 1.0) * speed)
        self.playback_timer = self.after(max(1, int((due - time.perf_counter()) * 1000)), self._play_next_frame)

    def _end_of_clip(self):
        self.is_playing = False
        self.play_clock = None
        self._stop_video_sound()
        self.play_pause_btn.configure(text="▶")

    def _play_next_frame(self):
        """Show the frame the clock is on, skipping frames if decoding falls behind."""
        if not self.is_playing or self.closed or not self.video or not self.play_clock:
            return
        started, first, speed = self.play_clock
        target = first + int((time.perf_counter() - started) * max(self.fps, 1.0) * speed)
        target = max(target, self.current_frame + 1)
        if target >= self.total_frames:
            self._end_of_clip()
            return
        behind = target - self.current_frame - 1
        if behind > self.fps * 2:
            frame = self.video.read(target)       # far behind: jump rather than decode every frame
        else:
            for _ in range(behind):
                self.video.skip()
            frame = self.video.next()
        if frame is None:
            self._end_of_clip()
            return
        self._render_frame(frame, target)
        self._schedule_next_frame()

    def play_video(self):
        self.start_playback()

    def next_frame(self):
        """Go to next frame(s)"""
        if self.video and self.current_frame < self.total_frames - 1:
            step = self.step_var.get()
            self.display_frame(min(self.current_frame + step, self.total_frames - 1))

    def previous_frame(self):
        """Go to previous frame(s)"""
        if self.video and self.current_frame > 0:
            step = self.step_var.get()
            self.display_frame(max(self.current_frame - step, 0))

    def on_timeline_change(self, value):
        """Scrub. FFmpeg-decoded clips restart a decoder per frame, so drags are coalesced."""
        if self.current_media_type == "audio":
            self.seek_audio(float(value))
            return
        if not self.video or self.is_playing:
            return
        if isinstance(self.video, OpenCVVideo):
            self.display_frame(int(value))
            return
        if self.pending_seek is not None:
            self.after_cancel(self.pending_seek)
        self.pending_seek = self.after(60, self._apply_seek, int(value))

    def _apply_seek(self, frame_number):
        self.pending_seek = None
        self.display_frame(frame_number)

    def jump_to_frame(self):
        """Jump to a specific frame number"""
        try:
            frame_num = int(self.jump_entry.get())
            if self.video and 0 <= frame_num < self.total_frames:
                self.display_frame(frame_num)
            else:
                messagebox.showwarning("Invalid Frame", f"Frame number must be between 0 and {self.total_frames-1}")
        except ValueError:
            messagebox.showwarning("Invalid Input", "Please enter a valid frame number")

    def export_frame(self):
        """Export the current frame at full resolution."""
        if not self.video:
            return
        try:
            frame = self.video.full_frame(self.current_frame)
            if frame is None:
                return
            file_path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG files", "*.png"), ("JPEG files", "*.jpg"), ("TIFF files", "*.tif"),
                           ("All files", "*.*")],
                initialfile=f"frame_{self.current_frame}"
            )
            if file_path:
                Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).save(file_path)
                self.info_label.configure(text=f"Frame exported to: {os.path.basename(file_path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export frame: {str(e)}")

    def set_controls_for(self, kind, ready=True, file_selected=None):
        """Enable the controls that make sense for what's in the viewer."""
        video = kind == "video"
        playable = video or (kind == "audio" and ready)
        states = {
            self.play_pause_btn: playable, self.stop_btn: playable, self.timeline_slider: playable,
            self.prev_frame_btn: video, self.next_frame_btn: video, self.speed_menu: video,
            self.jump_entry: video, self.step_spin: video, self.export_btn: video,
            self.open_btn: bool(kind) or bool(file_selected), self.reveal_btn: bool(kind) or bool(file_selected),
        }
        for widget, enabled in states.items():
            widget.configure(state="normal" if enabled else "disabled")

    def set_video_controls_state(self, enabled):
        self.set_controls_for("video" if enabled else None)

    # ================================================================== outside apps
    def open_current_in_app(self):
        if self.current_video_path:
            self.open_media_file(self.current_video_path)

    def reveal_current(self):
        path = self.current_video_path
        if not path:
            return
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception as e:
            messagebox.showerror("Error", f"Could not show the file: {str(e)}")

    def open_media_file(self, file_path):
        """Open media file with default system application"""
        try:
            if platform.system() == 'Windows':
                os.startfile(file_path)
            elif platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', file_path])
            else:  # Linux
                subprocess.run(['xdg-open', file_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open file: {str(e)}")


def create_tool():
    return Tool()


def get_tool():
    return Tool()
