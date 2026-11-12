import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from PIL import Image, UnidentifiedImageError
from tkinter import filedialog, messagebox

from base_tool import BaseTool
from drag_drop import enable_path_drop
import theme
from ffmpeg_setup import configure_ffmpeg

KEEP_FORMAT = "original"
IMAGE_TARGETS = {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff"}
RESIZE_NONE = "No resize"
RESIZE_WIDTH = "Width (keep ratio)"
RESIZE_HEIGHT = "Height (keep ratio)"
RESIZE_PERCENT = "Percent"
RESIZE_FIT = "Fit inside box"
RESIZE_MODES = [RESIZE_NONE, RESIZE_WIDTH, RESIZE_HEIGHT, RESIZE_PERCENT, RESIZE_FIT]


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "File Converter"
        self.description = "Convert common image, audio, and video files between formats"
        self.category = "Media"
        self.icon = "arrow-right-left"

    def create_window(self, parent):
        window = self.create_tool_window(parent, "File Converter", 900, 800)
        window.minsize(760, 640)
        self.window = window

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))

        actions = theme.action_bar(page)
        theme.Button(actions, "Clear", kind="ghost", width=76, command=self.clear_fields).pack(side="left")
        theme.Button(actions, "Convert", kind="primary", width=140, command=self.run_conversion).pack(side="right")

        # --- Source, output, format -----------------------------------------
        settings = theme.Card(page, title="Source & output")
        settings.pack(fill="x")
        main = settings.body
        main.grid_columnconfigure(1, weight=1)

        theme.muted(main, "Source", role="body", width=110).grid(row=0, column=0, sticky="w")
        self.source_entry = ctk.CTkEntry(main, placeholder_text="A file, or a folder of files", height=34)
        self.source_entry.grid(row=0, column=1, padx=(0, 8), sticky="ew")
        theme.Button(main, "File", kind="secondary", width=80, command=self.browse_source_file).grid(row=0, column=2)
        theme.Button(main, "Folder", kind="secondary", width=86, command=self.browse_source_folder).grid(row=0, column=3, padx=(8, 0))

        theme.muted(main, "Output", role="body", width=110).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.output_entry = ctk.CTkEntry(main, placeholder_text="Optional: defaults to a 'converted' folder next to the source", height=34)
        self.output_entry.grid(row=1, column=1, padx=(0, 8), pady=(10, 0), sticky="ew")
        theme.Button(main, "Choose", kind="secondary", width=174, command=self.browse_output_folder).grid(
            row=1, column=2, columnspan=2, pady=(10, 0), sticky="ew")

        theme.muted(main, "Convert to", role="body", width=110).grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.target_var = tk.StringVar(value="mp4")
        self.target_menu = ctk.CTkOptionMenu(
            main,
            variable=self.target_var,
            values=[KEEP_FORMAT, "mp4", "mov", "avi", "mkv", "webm", "mp3", "wav", "aac", "flac", "ogg", "jpg", "png", "webp", "bmp", "gif", "tif"],
            width=180,
            height=34,
        )
        self.target_menu.grid(row=2, column=1, pady=(10, 0), sticky="w")

        # --- Image resize -----------------------------------------------------
        resize_card = theme.Card(page, title="Image resize (applies to every image in the batch)")
        resize_card.pack(fill="x", pady=(14, 0))
        rs = resize_card.body
        theme.muted(rs, "Resize", role="body", width=110).grid(row=0, column=0, sticky="w")
        self.resize_mode_var = tk.StringVar(value=RESIZE_NONE)
        ctk.CTkOptionMenu(rs, variable=self.resize_mode_var, values=RESIZE_MODES, width=180, height=34,
                          command=lambda _v: self._update_resize_fields()).grid(row=0, column=1, sticky="w")
        self.size_a = ctk.CTkEntry(rs, width=90, height=34, justify="center")
        self.size_a.grid(row=0, column=2, padx=(10, 0))
        self.size_x = theme.muted(rs, "Ã—", role="body", width=16)
        self.size_x.grid(row=0, column=3)
        self.size_b = ctk.CTkEntry(rs, width=90, height=34, justify="center")
        self.size_b.grid(row=0, column=4)
        self.size_unit = theme.muted(rs, "", role="body")
        self.size_unit.grid(row=0, column=5, padx=(8, 0), sticky="w")

        theme.muted(rs, "JPEG/WebP quality", role="body", width=110).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.quality_var = tk.IntVar(value=90)
        ctk.CTkSlider(rs, from_=40, to=100, number_of_steps=60, variable=self.quality_var, width=180,
                      command=lambda v: self.quality_label.configure(text=str(int(float(v))))
                      ).grid(row=1, column=1, pady=(10, 0), sticky="w")
        self.quality_label = theme.muted(rs, "90", role="body", width=30)
        self.quality_label.grid(row=1, column=2, pady=(10, 0), sticky="w", padx=(10, 0))
        self.no_enlarge_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(rs, text="Don't enlarge smaller images", variable=self.no_enlarge_var).grid(
            row=1, column=3, columnspan=3, pady=(10, 0), sticky="w", padx=(10, 0))
        self._update_resize_fields()

        # --- Log --------------------------------------------------------------
        log_card = theme.Card(page, title="Log")
        log_card.pack(fill="both", expand=True, pady=(14, 0))
        self.log_box = theme.LogBox(log_card.body, wrap="word")
        self.log_box.pack(fill="both", expand=True)

        self.sources = []
        self._source_text = ""
        self.log("Ready. Select or drag in files or folders (several at once is fine), choose a format, and convert.")
        enable_path_drop(window, self.source_entry, self.drop_source, settings)
        enable_path_drop(window, self.output_entry, self.drop_output)
        return window

    def _update_resize_fields(self):
        mode = self.resize_mode_var.get()
        labels = {RESIZE_NONE: None, RESIZE_WIDTH: ("", None, "px wide"), RESIZE_HEIGHT: ("", None, "px tall"),
                  RESIZE_PERCENT: ("", None, "%"), RESIZE_FIT: ("", "", "px max (width Ã— height)")}
        spec = labels[mode]
        state = "disabled" if spec is None else "normal"
        self.size_a.configure(state=state)
        self.size_b.configure(state="normal" if mode == RESIZE_FIT else "disabled")
        if spec is None:
            self.size_x.grid_remove()
            self.size_unit.configure(text="")
        else:
            self.size_x.grid() if mode == RESIZE_FIT else self.size_x.grid_remove()
            self.size_unit.configure(text=spec[2])

    def _set_sources(self, paths):
        self.sources = list(paths)
        text = paths[0] if len(paths) == 1 else f"{len(paths)} items: " + "; ".join(os.path.basename(p) for p in paths)
        self._source_text = text
        self.source_entry.delete(0, tk.END)
        self.source_entry.insert(0, text)

    def _get_sources(self):
        text = self.source_entry.get().strip()
        if self.sources and text == self._source_text:
            return list(self.sources)
        return [text] if text else []

    def drop_source(self, paths):
        self._set_sources(paths)

    def drop_output(self, paths):
        folder = next((p for p in paths if os.path.isdir(p)), None)
        if folder is None:
            self.log("Drop a folder to use as the output location.")
            return
        self.output_entry.delete(0, tk.END)
        self.output_entry.insert(0, folder)

    def clear_fields(self):
        self.source_entry.delete(0, tk.END)
        self.sources = []
        self.output_entry.delete(0, tk.END)
        self.log_box.delete("1.0", tk.END)
        self.log("Fields cleared.")

    def log(self, text):
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.see(tk.END)

    def browse_source_file(self):
        file_paths = filedialog.askopenfilenames(title="Choose files to convert")
        if file_paths:
            self._set_sources(file_paths)

    def browse_source_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder to convert")
        if folder:
            self._set_sources([folder])

    def browse_output_folder(self):
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)

    def _ffmpeg_path(self):
        ffmpeg_path = configure_ffmpeg()
        if ffmpeg_path:
            return str(ffmpeg_path)
        return shutil_which("ffmpeg") or shutil_which("ffmpeg.exe")

    def _supported_files(self, roots):
        if isinstance(roots, str):
            roots = [roots]
        supported = []
        for root in roots:
            if os.path.isdir(root):
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d != "converted"]
                    for name in filenames:
                        if self._file_category(name) is not None:
                            supported.append(os.path.join(dirpath, name))
            elif os.path.isfile(root):
                if self._file_category(root) is not None:
                    supported.append(root)
        return sorted(dict.fromkeys(supported))

    def _file_category(self, filename):
        lower = filename.lower()
        if lower.endswith(tuple(self._image_extensions())):
            return "image"
        if lower.endswith(tuple(self._audio_extensions())):
            return "audio"
        if lower.endswith(tuple(self._video_extensions())):
            return "video"
        return None

    def _image_extensions(self):
        return [".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"]

    def _audio_extensions(self):
        return [".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a"]

    def _video_extensions(self):
        return [".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v"]

    def _target_for(self, category, target_ext):
        target_ext = target_ext.lower().lstrip(".")
        if target_ext == KEEP_FORMAT:
            return "image" if category == "image" else None
        if target_ext in {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff"}:
            return "image" if category == "image" else None
        if category == "image":
            return None
        if category in {"audio", "video"}:
            if target_ext in {"mp3", "wav", "aac", "flac", "ogg", "m4a"}:
                return "audio"
            if target_ext in {"mp4", "mov", "avi", "mkv", "webm", "wmv", "flv", "m4v"}:
                return "video"
        return None

    def run_conversion(self):
        sources = self._get_sources()
        output_root = self.output_entry.get().strip()
        target_ext = "." + self.target_var.get().strip().lower().lstrip(".")
        keep_format = target_ext == "." + KEEP_FORMAT

        if not sources:
            messagebox.showerror("Missing input", "Please select files or a folder to convert.")
            return

        resize = self._read_resize()
        if resize is False:
            return

        if not output_root:
            first = sources[0]
            output_root = os.path.join(os.path.dirname(first) if os.path.isfile(first) else first, "converted")

        files = self._supported_files(sources)
        if not files:
            messagebox.showwarning("No supported files", "No supported image, audio, or video files were found in the selected source.")
            return

        os.makedirs(output_root, exist_ok=True)

        ffmpeg_exe = None
        if any(self._file_category(f) != "image" for f in files) and not keep_format \
                and target_ext.lstrip(".") not in IMAGE_TARGETS:
            ffmpeg_exe = self._ffmpeg_path()
            if ffmpeg_exe is None:
                messagebox.showerror("Missing ffmpeg", "FFmpeg is not available. Ensure ffmpeg is installed or bundled with the project.")
                return

        self.log(f"Found {len(files)} supported file(s) to convert. Output folder: {output_root}")
        self.log(f"Target format: {'same as original' if keep_format else target_ext}")
        if resize:
            self.log(f"Resize: {self.resize_mode_var.get()} {resize[1]}{' x ' + str(resize[2]) if resize[2] else ''}")

        converted = 0
        used = set()
        for index, file_path in enumerate(files, start=1):
            category = self._file_category(file_path)
            if category is None:
                continue

            if self._target_for(category, target_ext) != category:
                self.log(f"[{index}/{len(files)}] Skipped: {os.path.basename(file_path)} (selected output {target_ext} does not match {category} files)")
                continue

            try:
                ext = os.path.splitext(file_path)[1].lower() if keep_format else target_ext
                base = os.path.splitext(os.path.basename(file_path))[0]
                out_path = os.path.join(output_root, base + ext)
                n = 2
                while out_path.lower() in used or os.path.exists(out_path):
                    out_path = os.path.join(output_root, f"{base} ({n}){ext}")
                    n += 1
                used.add(out_path.lower())
                if category == "image":
                    self._convert_image(file_path, out_path, ext, resize)
                else:
                    self._convert_media(file_path, out_path, category, target_ext, ffmpeg_exe)
                converted += 1
                self.log(f"[{index}/{len(files)}] Converted: {os.path.basename(file_path)} -> {os.path.basename(out_path)}")
            except Exception as exc:
                self.log(f"[{index}/{len(files)}] Failed: {os.path.basename(file_path)} | {exc}")
            self.window.update_idletasks()

        self.log(f"Done. Converted {converted} file(s).")
        messagebox.showinfo("Conversion completed", f"Converted {converted} file(s) in:\n{output_root}")

    def _read_resize(self):
        """None for no resize, False for invalid input, else (mode, a, b)."""
        mode = self.resize_mode_var.get()
        if mode == RESIZE_NONE:
            return None
        try:
            a = float(self.size_a.get())
            b = float(self.size_b.get()) if mode == RESIZE_FIT else None
            if a <= 0 or (b is not None and b <= 0):
                raise ValueError
        except ValueError:
            messagebox.showerror("Resize", "Enter positive numbers for the resize size.")
            return False
        return (mode, a, b)

    def _target_size(self, size, resize):
        w, h = size
        mode, a, b = resize
        if mode == RESIZE_WIDTH:
            scale = a / w
        elif mode == RESIZE_HEIGHT:
            scale = a / h
        elif mode == RESIZE_PERCENT:
            scale = a / 100
        else:
            scale = min(a / w, b / h)
        if scale > 1 and self.no_enlarge_var.get():
            return size
        return max(1, round(w * scale)), max(1, round(h * scale))

    def _convert_image(self, in_path, out_path, target_ext, resize=None):
        fmt = self._image_format_for(target_ext)
        try:
            with Image.open(in_path) as img:
                if img.mode in {"RGBA", "LA", "P"}:
                    img = img.convert("RGBA")
                elif img.mode not in {"RGB", "L", "CMYK"}:
                    img = img.convert("RGB")
                if fmt in {"JPEG", "BMP"} and img.mode in {"RGBA", "LA"}:
                    flat = Image.new("RGB", img.size, "white")
                    flat.paste(img, mask=img.getchannel("A"))
                    img = flat
                elif fmt == "JPEG" and img.mode == "CMYK":
                    pass
                if resize:
                    new_size = self._target_size(img.size, resize)
                    if new_size != img.size:
                        img = img.resize(new_size, Image.LANCZOS)
                options = {}
                if fmt in {"JPEG", "WEBP"}:
                    options["quality"] = int(self.quality_var.get())
                img.save(out_path, format=fmt, **options)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise RuntimeError(str(exc)) from exc

    def _image_format_for(self, target_ext):
        ext = target_ext.lower().lstrip(".")
        mapping = {
            "jpg": "JPEG",
            "jpeg": "JPEG",
            "png": "PNG",
            "webp": "WEBP",
            "bmp": "BMP",
            "gif": "GIF",
            "tif": "TIFF",
            "tiff": "TIFF",
        }
        return mapping.get(ext, "PNG")

    def _convert_media(self, in_path, out_path, category, target_ext, ffmpeg_exe):
        target_ext = target_ext.lower().lstrip(".")

        if category == "audio":
            cmd = [
                ffmpeg_exe,
                "-y",
                "-i",
                in_path,
                "-vn",
                "-acodec",
                self._audio_codec_for(target_ext),
                "-ar",
                "44100",
                out_path,
            ]
        else:
            cmd = [
                ffmpeg_exe,
                "-y",
                "-i",
                in_path,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                out_path,
            ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _audio_codec_for(self, target_ext):
        mapping = {
            "mp3": "libmp3lame",
            "wav": "pcm_s16le",
            "aac": "aac",
            "flac": "flac",
            "ogg": "libvorbis",
            "m4a": "aac",
        }
        return mapping.get(target_ext, "libmp3lame")


def shutil_which(name):
    import shutil
    return shutil.which(name)


def create_tool():
    return Tool()


def get_tool():
    return Tool()
