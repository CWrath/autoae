import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from PIL import Image, UnidentifiedImageError
from tkinter import filedialog, messagebox

from base_tool import BaseTool
import theme
from ffmpeg_setup import configure_ffmpeg


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "File Converter"
        self.description = "Convert common image, audio, and video files between formats"
        self.category = "Media"
        self.icon = "arrow-right-left"

    def create_window(self, parent):
        window = self.create_tool_window(parent, "File Converter", 900, 700)
        window.minsize(760, 560)
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
        theme.Button(main, "File…", kind="secondary", width=80, command=self.browse_source_file).grid(row=0, column=2)
        theme.Button(main, "Folder…", kind="secondary", width=86, command=self.browse_source_folder).grid(row=0, column=3, padx=(8, 0))

        theme.muted(main, "Output", role="body", width=110).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.output_entry = ctk.CTkEntry(main, placeholder_text="Optional: defaults to a 'converted' folder next to the source", height=34)
        self.output_entry.grid(row=1, column=1, padx=(0, 8), pady=(10, 0), sticky="ew")
        theme.Button(main, "Choose…", kind="secondary", width=174, command=self.browse_output_folder).grid(
            row=1, column=2, columnspan=2, pady=(10, 0), sticky="ew")

        theme.muted(main, "Convert to", role="body", width=110).grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.target_var = tk.StringVar(value="mp4")
        self.target_menu = ctk.CTkOptionMenu(
            main,
            variable=self.target_var,
            values=["mp4", "mov", "avi", "mkv", "webm", "mp3", "wav", "aac", "flac", "ogg", "jpg", "png", "webp", "bmp", "gif"],
            width=180,
            height=34,
        )
        self.target_menu.grid(row=2, column=1, pady=(10, 0), sticky="w")

        # --- Log --------------------------------------------------------------
        log_card = theme.Card(page, title="Log")
        log_card.pack(fill="both", expand=True, pady=(14, 0))
        self.log_box = theme.LogBox(log_card.body, wrap="word")
        self.log_box.pack(fill="both", expand=True)

        self.log("Ready. Select a file or folder, choose an output format, and convert.")
        return window

    def clear_fields(self):
        self.source_entry.delete(0, tk.END)
        self.output_entry.delete(0, tk.END)
        self.log_box.delete("1.0", tk.END)
        self.log("Fields cleared.")

    def log(self, text):
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.see(tk.END)

    def browse_source_file(self):
        file_path = filedialog.askopenfilename(title="Choose a file to convert")
        if file_path:
            self.source_entry.delete(0, tk.END)
            self.source_entry.insert(0, file_path)

    def browse_source_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder to convert")
        if folder:
            self.source_entry.delete(0, tk.END)
            self.source_entry.insert(0, folder)

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

    def _supported_files(self, root):
        supported = []
        if os.path.isdir(root):
            for dirpath, _, filenames in os.walk(root):
                for name in filenames:
                    if self._file_category(name) is not None:
                        supported.append(os.path.join(dirpath, name))
        elif os.path.isfile(root):
            if self._file_category(root) is not None:
                supported.append(root)
        return sorted(supported)

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
        if category == "image":
            return "image"
        if category in {"audio", "video"}:
            if target_ext in {"mp3", "wav", "aac", "flac", "ogg", "m4a"}:
                return "audio"
            if target_ext in {"mp4", "mov", "avi", "mkv", "webm", "wmv", "flv", "m4v"}:
                return "video"
        return None

    def run_conversion(self):
        source = self.source_entry.get().strip()
        output_root = self.output_entry.get().strip()
        target_ext = "." + self.target_var.get().strip().lower().lstrip(".")

        if not source:
            messagebox.showerror("Missing input", "Please select a file or folder to convert.")
            return

        if not output_root:
            output_root = os.path.join(os.path.dirname(source) if os.path.isfile(source) else source, "converted")

        os.makedirs(output_root, exist_ok=True)

        files = self._supported_files(source)
        if not files:
            messagebox.showwarning("No supported files", "No supported image, audio, or video files were found in the selected source.")
            return

        ffmpeg_exe = self._ffmpeg_path()
        if ffmpeg_exe is None:
            messagebox.showerror("Missing ffmpeg", "FFmpeg is not available. Ensure ffmpeg is installed or bundled with the project.")
            return

        self.log(f"Found {len(files)} supported file(s) to convert. Output folder: {output_root}")
        self.log(f"Target format: {target_ext}")

        converted = 0
        for index, file_path in enumerate(files, start=1):
            category = self._file_category(file_path)
            if category is None:
                continue

            if self._target_for(category, target_ext) != category:
                self.log(f"[{index}/{len(files)}] Skipped: {os.path.basename(file_path)} (selected output {target_ext} does not match {category} files)")
                continue

            try:
                out_name = os.path.splitext(os.path.basename(file_path))[0] + target_ext
                out_path = os.path.join(output_root, out_name)
                if category == "image":
                    self._convert_image(file_path, out_path, target_ext)
                else:
                    self._convert_media(file_path, out_path, category, target_ext, ffmpeg_exe)
                converted += 1
                self.log(f"[{index}/{len(files)}] Converted: {os.path.basename(file_path)} -> {os.path.basename(out_path)}")
            except Exception as exc:
                self.log(f"[{index}/{len(files)}] Failed: {os.path.basename(file_path)} | {exc}")

        self.log(f"Done. Converted {converted} file(s).")
        messagebox.showinfo("Conversion completed", f"Converted {converted} file(s) to {target_ext} format in:\n{output_root}")

    def _convert_image(self, in_path, out_path, target_ext):
        try:
            with Image.open(in_path) as img:
                if img.mode in {"RGBA", "LA", "P"}:
                    img = img.convert("RGBA")
                elif img.mode not in {"RGB", "L", "CMYK"}:
                    img = img.convert("RGB")
                img.save(out_path, format=self._image_format_for(target_ext))
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
