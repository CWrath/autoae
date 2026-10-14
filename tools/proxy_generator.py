import customtkinter as ctk
from base_tool import BaseTool
import theme
from theme import C
from tkinter import filedialog, messagebox
import threading
import subprocess
import time
import os
import sys
import json
import cv2
import shutil
import re
from ffmpeg_setup import configure_ffmpeg

class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Proxy Generator"  # This appears in the menu
        self.description = "Generate proxy files for video editing"
        self.category = "Media"
        self.icon = "layers"

        # Initialize variables
        self.input_files = []
        self.output_folder = ""
        self.thread = None
        self.cancel_flag = False
        self.pause_flag = False
        self.current_process = None
        self.parent_window = None
        self.closed = False
        self.queue_rows = {}

        self.ffmpeg_path = configure_ffmpeg()
        if self.ffmpeg_path is None:
            messagebox.showwarning(
                "FFmpeg not found",
                "FFmpeg is required, but it could not be found in the application bundle or system PATH.",
            )

    def create_window(self, parent):
        self.parent_window = parent
        self.closed = False
        window = self.create_tool_window(parent, "Proxy Generator", 960, 820)
        window.minsize(860, 620)

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))

        # --- Actions ------------------------------------------------------------
        button_frame = theme.action_bar(page)
        theme.Button(button_frame, "Save Queue…", kind="ghost", width=110, command=self.save_queue).pack(side="left")
        theme.Button(button_frame, "Load Queue…", kind="ghost", width=110, command=self.load_queue).pack(side="left")
        theme.Button(button_frame, "Start", kind="primary", width=120, command=self.start_thread).pack(side="right")
        theme.Button(button_frame, "Resume", kind="secondary", width=90, command=self.resume).pack(side="right", padx=(0, 8))
        theme.Button(button_frame, "Pause", kind="secondary", width=86, command=self.pause).pack(side="right", padx=(0, 8))
        theme.Button(button_frame, "Cancel", kind="danger", width=86, command=self.cancel).pack(side="right", padx=(0, 8))

        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x")
        top.grid_columnconfigure((0, 1), weight=1, uniform="top")

        # --- Source & destination ---------------------------------------------
        source_card = theme.Card(top, title="Source & destination")
        source_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        source_frame = source_card.body
        source_frame.grid_columnconfigure((0, 1), weight=1)

        theme.overline(source_frame, "Input").grid(row=0, column=0, columnspan=2, sticky="w")
        self.input_label = ctk.CTkLabel(source_frame, text="No input selected", anchor="w",
                                        justify="left", wraplength=360, height=24)
        self.input_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        theme.Button(source_frame, "Select Files…", kind="secondary", command=self.select_files).grid(
            row=2, column=0, sticky="ew", padx=(0, 4))
        theme.Button(source_frame, "Select Folder…", kind="secondary", command=self.select_folder).grid(
            row=2, column=1, sticky="ew", padx=(4, 0))
        theme.muted(source_frame, "Folders only pick up clips over 1080p").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        theme.divider(source_frame).grid(row=4, column=0, columnspan=2, sticky="ew", pady=14)

        theme.overline(source_frame, "Output").grid(row=5, column=0, columnspan=2, sticky="w")
        self.output_label = ctk.CTkLabel(
            source_frame, text="No output folder selected", anchor="w", justify="left", wraplength=360, height=24
        )
        self.output_label.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        theme.Button(
            source_frame, "Choose Output Folder…", kind="secondary", command=self.choose_output_folder
        ).grid(row=7, column=0, columnspan=2, sticky="ew")

        # --- Settings --------------------------------------------------------------
        settings_card = theme.Card(top, title="Proxy settings")
        settings_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        settings_frame = settings_card.body

        self.resolution_var = ctk.StringVar(value="1280x720")
        theme.form_row(settings_frame, 0, "Resolution", ctk.CTkOptionMenu(
            settings_frame,
            variable=self.resolution_var,
            values=["1920x1080", "1280x720", "640x360", "Original"],
            height=32,
        ), label_width=110, pady=(0, 8))

        self.frame_rate_var = ctk.StringVar(value="Same as Source")
        theme.form_row(settings_frame, 1, "Frame rate", ctk.CTkOptionMenu(
            settings_frame,
            variable=self.frame_rate_var,
            values=["Same as Source", "23.976", "24", "25", "29.97", "30", "50", "59.94", "60"],
            height=32,
        ), label_width=110, pady=(0, 8))

        self.video_codec_var = ctk.StringVar(value="H.264")
        theme.form_row(settings_frame, 2, "Video codec", ctk.CTkOptionMenu(
            settings_frame,
            variable=self.video_codec_var,
            values=[
                "H.264",
                "ProRes Proxy",
                "ProRes LT",
                "ProRes 422",
                "ProRes HQ",
                "ProRes HQX",
                "DNxHR LB",
            ],
            height=32,
        ), label_width=110, pady=(0, 8))

        self.bitrate_var = ctk.StringVar(value="5M")
        theme.form_row(settings_frame, 3, "H.264 bitrate",
                       ctk.CTkEntry(settings_frame, textvariable=self.bitrate_var, width=120, height=32),
                       label_width=110, pady=(0, 8), sticky="w")

        self.audio_var = ctk.StringVar(value="copy")
        theme.form_row(settings_frame, 4, "Audio", ctk.CTkOptionMenu(
            settings_frame,
            variable=self.audio_var,
            values=["copy", "AAC", "none", "Mono PCM", "Stereo PCM"],
            height=32,
        ), label_width=110, pady=(0, 8))

        self.export_mode_var = ctk.StringVar(value="Video + Audio")
        theme.form_row(settings_frame, 5, "Export mode", ctk.CTkOptionMenu(
            settings_frame,
            variable=self.export_mode_var,
            values=["Video + Audio", "Audio Only"],
            height=32,
        ), label_width=110, pady=(0, 0))

        # --- Progress ---------------------------------------------------------------
        progress_card = theme.Card(page, title="Progress")
        progress_card.pack(fill="x", pady=(14, 0))
        self.progress_label = ctk.CTkLabel(progress_card.header, text="0%", font=theme.font("caption"),
                                           text_color=C["text_muted"], height=20)
        self.progress_label.pack(side="right")
        self.status_label = ctk.CTkLabel(progress_card.body, text="Ready", anchor="w", height=22)
        self.status_label.pack(fill="x")
        self.progress_bar = theme.ProgressBar(progress_card.body)
        self.progress_bar.pack(fill="x", pady=(6, 0))

        # --- Queue --------------------------------------------------------------------
        queue_card = theme.Card(page, title="Queue")
        queue_card.pack(fill="both", expand=True, pady=(14, 0))
        self.queue_list = ctk.CTkScrollableFrame(queue_card.body, height=120, fg_color="transparent")
        self.queue_list.pack(fill="both", expand=True)
        self.refresh_queue_display()

        return window

    def select_files(self):
        files = filedialog.askopenfilenames(title="Select Video Files", filetypes=[("Video Files", "*.mp4 *.mov *.mkv")])
        self.input_files = list(files)
        self.input_label.configure(text=f"{len(self.input_files)} files selected")
        self.refresh_queue_display()

    def select_folder(self):
        folder = filedialog.askdirectory(title="Select Folder with Video Files")
        if folder:
            self.input_files = [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.lower().endswith((".mp4", ".mov", ".mkv")) and self.is_high_res(os.path.join(folder, f))
            ]
            self.input_label.configure(text=f"{len(self.input_files)} high-res files selected")
            self.refresh_queue_display()

    def choose_output_folder(self):
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.output_folder = folder
            self.output_label.configure(text=f"Output: {folder}")

    # Utility methods
    def is_high_res(self, filepath):
        try:
            cap = cv2.VideoCapture(filepath)
            width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            cap.release()
            return max(width, height) > 1080
        except:
            return True  # Assume high-res if uncertain

    def is_ffmpeg_available(self):
        return shutil.which("ffmpeg") is not None

    # Control methods
    def pause(self):
        self.pause_flag = True
        self.status_label.configure(text="Paused...", text_color=C["warning"])

    def resume(self):
        self.pause_flag = False
        self.status_label.configure(text="Resuming...", text_color=C["warning"])

    def cancel(self):
        self.cancel_flag = True
        self.status_label.configure(text="Cancelling...", text_color=C["danger"])
        if self.current_process and self.current_process.poll() is None:
            print("Terminating active FFmpeg process")
            self.current_process.terminate()
            try:
                self.current_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                print("FFmpeg did not exit after termination; killing process")
                self.current_process.kill()

    def close_window(self):
        self.closed = True
        self.cancel()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def schedule_ui(self, callback, *args):
        if self.parent_window and not self.closed:
            self.parent_window.after(0, self.run_ui_callback, callback, args)

    def run_ui_callback(self, callback, args):
        if not self.closed:
            callback(*args)

    def save_queue(self):
        if not self.input_files:
            self.status_label.configure(text="Nothing to save", text_color=C["danger"])
            return
        save_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if save_path:
            with open(save_path, "w") as f:
                json.dump({"input_files": self.input_files, "output_folder": self.output_folder}, f)
            self.status_label.configure(text="Queue saved", text_color=C["success"])

    def load_queue(self):
        load_path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if load_path:
            with open(load_path, "r") as f:
                data = json.load(f)
                self.input_files = data.get("input_files", [])
                self.output_folder = data.get("output_folder", "")
                self.input_label.configure(text=f"{len(self.input_files)} files loaded")
                self.output_label.configure(text=f"Output: {self.output_folder}")
                self.refresh_queue_display()

    def refresh_queue_display(self):
        if not hasattr(self, "queue_list"):
            return
        for widget in self.queue_list.winfo_children():
            widget.destroy()
        self.queue_rows = {}
        for index, filepath in enumerate(self.input_files):
            row = ctk.CTkFrame(self.queue_list, fg_color=C["surface_2"], corner_radius=8)
            row.pack(fill="x", padx=(0, 4), pady=3)
            row.grid_columnconfigure(0, weight=1)
            name = ctk.CTkLabel(row, text=os.path.basename(filepath), anchor="w", height=22)
            name.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
            status = ctk.CTkLabel(row, text="Queued", width=130, anchor="e", height=22,
                                  font=theme.font("caption"), text_color=C["text_muted"])
            status.grid(row=0, column=1, padx=10, pady=(6, 0))
            progress = theme.ProgressBar(row, height=6)
            progress.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 8))
            progress.set(0)
            self.queue_rows[filepath] = (status, progress)

    def update_queue_item(self, filepath, status_text, percent=None, color=C["text"]):
        row = self.queue_rows.get(filepath)
        if row is None:
            return
        status, progress = row
        status.configure(text=status_text, text_color=color)
        if percent is not None:
            progress.set(max(0, min(100, percent)) / 100)

    # Threading and conversion logic
    def start_thread(self):
        self.cancel_flag = False
        self.pause_flag = False
        self.update_progress(0)
        self.status_label.configure(text="Starting...", text_color=C["text"])
        print("Starting proxy generation")
        # daemon: a conversion must never keep Auto AE running after its window closes
        self.thread = threading.Thread(target=self.start_conversion, daemon=True)
        self.thread.start()

    def get_output_extension(self, video_codec, export_mode):
        if export_mode == "Audio Only":
            return "wav"
        if video_codec == "DNxHR LB":
            return "mxf"
        return "mov" if video_codec.startswith("ProRes") else "mp4"

    def get_ffmpeg_command(
        self, file, resolution, frame_rate, codec_name, audio_opt, out_path, export_mode
    ):
        video_codec = {
            "H.264": "libx264",
            "ProRes Proxy": "prores_ks",
            "ProRes LT": "prores_ks",
            "ProRes 422": "prores_ks",
            "ProRes HQ": "prores_ks",
            "ProRes HQX": "prores_ks",
            "DNxHR LB": "dnxhd",
        }[codec_name]
        bitrate = self.bitrate_var.get().strip()

        audio_flag = []
        audio_only = export_mode == "Audio Only"
        ext = self.get_output_extension(codec_name, export_mode)
        if audio_opt in ("Mono PCM", "Stereo PCM"):
            channels = "1" if audio_opt == "Mono PCM" else "2"
            audio_codec = "pcm_s16le" if audio_only or codec_name != "H.264" else "aac"
            audio_flag = ["-c:a", audio_codec, "-ac", channels, "-ar", "48000"]
        elif audio_only:
            audio_flag = ["-vn", "-c:a", "pcm_s16le", "-ar", "48000"]
        elif audio_opt == "AAC":
            audio_flag = ["-c:a", "aac"]
        elif audio_opt == "none":
            audio_flag = ["-an"]
        else:
            audio_flag = ["-c:a", "copy"]

        executable = str(self.ffmpeg_path) if self.ffmpeg_path else "ffmpeg"
        cmd = [executable, "-y", "-nostdin", "-i", file]
        if audio_only:
            cmd += ["-vn"]
        else:
            if resolution != "Original":
                cmd += ["-vf", f"scale={resolution}"]
            cmd += ["-c:v", video_codec]
            if frame_rate != "Same as Source":
                cmd += ["-r", frame_rate]
            if codec_name == "H.264":
                if not bitrate:
                    raise ValueError("H.264 bitrate cannot be empty")
                cmd += ["-b:v", bitrate, "-preset", "veryfast"]
            elif codec_name.startswith("ProRes"):
                prores_profile = {
                    "ProRes Proxy": "0",
                    "ProRes LT": "1",
                    "ProRes 422": "2",
                    "ProRes HQ": "3",
                    "ProRes HQX": "4",
                }[codec_name]
                pixel_format = "yuv444p10le" if codec_name == "ProRes HQX" else "yuv422p10le"
                cmd += ["-profile:v", prores_profile, "-pix_fmt", pixel_format]
            elif codec_name == "DNxHR LB":
                cmd += ["-profile:v", "dnxhr_lb", "-b:v", "45M"]
        cmd += audio_flag + ["-progress", "pipe:1", "-nostats", out_path]
        return cmd, ext

    def get_media_duration(self, filepath):
        """Read the source duration in seconds for progress reporting."""
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            bundled_ffprobe = self.ffmpeg_path.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if bundled_ffprobe.is_file():
                ffprobe = str(bundled_ffprobe)
        if ffprobe is None:
            raise FileNotFoundError("ffprobe is required to calculate transcoding progress")
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())

    def update_progress(self, percent):
        percent = max(0.0, min(100.0, percent))
        self.progress_bar.set(percent / 100)
        self.progress_label.configure(text=f"{percent:.1f}%")

    def open_output_location(self, output_path):
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(output_path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", output_path])  # reveal the file in Finder
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(output_path)])

    def start_conversion(self):
        if self.ffmpeg_path is None:
            self.status_label.configure(text="FFmpeg not found. Please install FFmpeg.", text_color=C["danger"])
            return

        if not self.input_files or not self.output_folder:
            self.status_label.configure(text="Missing inputs", text_color=C["danger"])
            return

        total = len(self.input_files)
        resolution = self.resolution_var.get()
        frame_rate = self.frame_rate_var.get()
        codec_name = self.video_codec_var.get()
        audio_opt = self.audio_var.get()
        export_mode = self.export_mode_var.get()

        for idx, file in enumerate(self.input_files):
            self.schedule_ui(self.update_queue_item, file, "Preparing...", 0, C["warning"])
            while self.pause_flag and not self.cancel_flag:  # Cancel or closing the app ends a pause
                time.sleep(0.1)
            if self.cancel_flag:
                for pending_file in self.input_files[idx:]:
                    self.schedule_ui(self.update_queue_item, pending_file, "Cancelled", None, C["danger"])
                self.schedule_ui(self.status_label.configure, {"text": "Cancelled", "text_color": C["danger"]})
                return

            base = os.path.splitext(os.path.basename(file))[0]
            ext = self.get_output_extension(codec_name, export_mode)
            out_file_name = f"{base}_proxy.{ext}"
            out_path = os.path.join(self.output_folder, out_file_name)
            cmd, ext = self.get_ffmpeg_command(
                file, resolution, frame_rate, codec_name, audio_opt, out_path, export_mode
            )
            try:
                duration = self.get_media_duration(file)
                print(f"Transcoding {idx + 1}/{total}: {file}")
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                self.current_process = process
                last_percent = -1.0
                output_lines = []
                for line in process.stdout:
                    output_lines.append(line)
                    match = re.match(r"out_time_us=(\d+)", line.strip())
                    if match and duration > 0:
                        file_percent = min(100.0, int(match.group(1)) / 1_000_000 / duration * 100)
                        overall_percent = (idx + file_percent / 100) / total * 100
                        if file_percent - last_percent >= 0.5 or file_percent >= 100:
                            last_percent = file_percent
                            print(f"  Progress: {overall_percent:.1f}% (file: {file_percent:.1f}%)")
                            self.schedule_ui(self.update_progress, overall_percent)
                            self.schedule_ui(
                                self.update_queue_item, file, f"{file_percent:.1f}%", file_percent, C["text"]
                            )
                return_code = process.wait()
                self.current_process = None
                if return_code:
                    raise subprocess.CalledProcessError(
                        return_code, cmd, stderr="".join(output_lines)
                    )
            except subprocess.CalledProcessError as error:
                error_output = error.stderr or ""
                details = error_output.strip().splitlines()
                reason = details[-1] if details else "FFmpeg returned an error"
                if self.cancel_flag:
                    for pending_file in self.input_files[idx:]:
                        self.schedule_ui(self.update_queue_item, pending_file, "Cancelled", None, C["danger"])
                    self.schedule_ui(self.status_label.configure, {"text": "Cancelled", "text_color": C["danger"]})
                    return
                print(f"Failed: {file}\n{error_output.strip()}")
                self.schedule_ui(
                    self.status_label.configure,
                    {"text": f"Failed: {os.path.basename(file)} ({reason[:100]})",
                     "text_color": C["danger"]},
                )
                self.schedule_ui(self.update_queue_item, file, "Failed", None, C["danger"])
                continue

            self.schedule_ui(self.update_progress, (idx + 1) / total * 100)
            print(f"Completed: {out_path}")
            self.open_output_location(out_path)
            self.schedule_ui(self.update_queue_item, file, "Complete", 100, C["success"])
            self.schedule_ui(
                self.status_label.configure,
                {"text": f"Complete: {os.path.basename(out_path)}", "text_color": C["success"]},
            )

        self.current_process = None
        self.schedule_ui(self.update_progress, 100)
        print("Proxy generation complete")
        self.schedule_ui(
            self.status_label.configure,
            {"text": "Proxy generation complete", "text_color": C["success"]},
        )
