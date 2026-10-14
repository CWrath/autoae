import customtkinter as ctk
import tkinter as tk
from base_tool import BaseTool
import theme
from theme import C

class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Timecode Calculator"
        self.description = "Calculate and convert timecodes"
        self.category = "Editorial & Turnover"
        self.icon = "timer"

    def create_window(self, parent):
        window = self.create_tool_window(parent, "Timecode Calculator", 560, 700)
        window.minsize(500, 660)

        main_frame = theme.page(window)
        theme.tool_header(main_frame, self, subtitle="Add or subtract timecode durations").pack(
            fill="x", pady=(0, 18))

        big_mono = theme.sized_font(24, "bold", mono=True)

        # --- Calculator ------------------------------------------------------
        calc = theme.Card(main_frame)
        calc.pack(fill="x")
        display_frame = calc.body
        display_frame.grid_columnconfigure((0, 1), weight=1, uniform="half")

        theme.overline(display_frame, "Frame rate").grid(row=0, column=0, sticky="w")
        theme.overline(display_frame, "Format").grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.fps_var = tk.StringVar(value="25")
        ctk.CTkOptionMenu(
            display_frame, variable=self.fps_var,
            values=["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"], height=34
        ).grid(row=1, column=0, padx=(0, 6), pady=(2, 14), sticky="ew")
        self.format_var = tk.StringVar(value="Non-Drop Frame")
        ctk.CTkOptionMenu(
            display_frame, variable=self.format_var,
            values=["Non-Drop Frame", "Drop Frame"], height=34
        ).grid(row=1, column=1, padx=(6, 0), pady=(2, 14), sticky="ew")

        theme.overline(display_frame, "Timecode 1").grid(row=2, column=0, sticky="w")
        self.tc1_entry = ctk.CTkEntry(
            display_frame, placeholder_text="01:00:00:00", justify="right",
            font=big_mono, height=48
        )
        self.tc1_entry.grid(row=3, column=0, columnspan=2, pady=(2, 10), sticky="ew")

        theme.overline(display_frame, "Operation").grid(row=4, column=0, sticky="w")
        self.operation_var = tk.StringVar(value="Add")
        operation_menu = ctk.CTkOptionMenu(
            display_frame, variable=self.operation_var, values=["Add", "Subtract"],
            width=130, height=34
        )
        operation_menu.grid(row=4, column=1, sticky="e")

        theme.overline(display_frame, "Timecode 2").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.tc2_entry = ctk.CTkEntry(
            display_frame, placeholder_text="00:30:00:00", justify="right",
            font=big_mono, height=48
        )
        self.tc2_entry.grid(row=6, column=0, columnspan=2, pady=(2, 14), sticky="ew")

        action_frame = ctk.CTkFrame(display_frame, fg_color="transparent")
        action_frame.grid(row=7, column=0, columnspan=2, sticky="ew")
        theme.Button(action_frame, "Clear", kind="ghost", width=90,
                     command=self.clear_fields).pack(side="left")
        theme.Button(action_frame, "=", kind="primary", height=42,
                     font=theme.sized_font(20, "bold"),
                     command=self.calculate_timecode).pack(side="right", fill="x", expand=True, padx=(12, 0))

        result_frame = ctk.CTkFrame(display_frame, corner_radius=10, fg_color=C["surface_2"])
        result_frame.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        theme.overline(result_frame, "Result").pack(anchor="w", padx=14, pady=(10, 0))
        self.result_label = ctk.CTkLabel(
            result_frame, text="--:--:--:--", font=theme.sized_font(28, "bold", mono=True)
        )
        self.result_label.pack(anchor="e", padx=14, pady=(0, 10))

        # --- Duration to frames ---------------------------------------------
        converter = theme.Card(main_frame, title="Duration to frames")
        converter.pack(fill="x", pady=(14, 0))
        converter_frame = converter.body
        converter_frame.grid_columnconfigure(0, weight=1)
        self.duration_entry = ctk.CTkEntry(
            converter_frame, placeholder_text="HH:MM:SS:FF", font=theme.sized_font(16, mono=True), height=36
        )
        self.duration_entry.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        theme.Button(
            converter_frame, "Convert", kind="secondary", width=100, command=self.convert_to_frames
        ).grid(row=0, column=1)
        self.frames_label = theme.muted(converter_frame, "Frames: --", role="body")
        self.frames_label.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="w")

        self.window = window

        return window

    def _show_result(self, text, error=False):
        """Big mono readout for timecodes; smaller red text for errors so they fit."""
        if error:
            self.result_label.configure(text=text, font=theme.font("body_strong"), text_color=C["danger"],
                                        wraplength=420, justify="right")
        else:
            self.result_label.configure(text=text, font=theme.sized_font(28, "bold", mono=True),
                                        text_color=C["text"])
    
    def calculate_timecode(self):
        """Calculate timecode addition/subtraction"""
        try:
            tc1 = self.tc1_entry.get()
            tc2 = self.tc2_entry.get()
            operation = self.operation_var.get()
            fps = float(self.fps_var.get())
            drop_frame = self.format_var.get() == "Drop Frame"
            
            frames_per_second = self._nominal_fps(fps)
            if drop_frame and fps not in (29.97, 30):
                raise ValueError("Drop frame is available only at 29.97 or 30 fps")

            frames1 = self._timecode_to_frames(tc1, frames_per_second, drop_frame)
            frames2 = self._timecode_to_frames(tc2, frames_per_second, drop_frame)

            if operation == "Add":
                result_frames = frames1 + frames2
            else:
                result_frames = frames1 - frames2

            result_tc = self._frames_to_timecode(result_frames, frames_per_second, drop_frame)
            self._show_result(result_tc)
            self.tc1_entry.delete(0, "end")
            self.tc1_entry.insert(0, result_tc)
            self.tc2_entry.delete(0, "end")
            self.tc2_entry.focus_set()

        except ValueError as e:
            self._show_result(f"Error: {str(e)}", error=True)
        except Exception:
            self._show_result("Error: Invalid input", error=True)

    @staticmethod
    def _nominal_fps(fps):
        """Return the integer frame number base used by a timecode label."""
        return int(round(fps))

    @classmethod
    def _timecode_to_frames(cls, timecode, frames_per_second, drop_frame):
        """Convert a timecode label into an absolute frame number."""
        sign = -1 if timecode.startswith("-") else 1
        if sign < 0:
            timecode = timecode[1:]
        parts = timecode.split(':')
        if len(parts) != 4:
            raise ValueError("Invalid timecode format. Use HH:MM:SS:FF")

        try:
            h, m, s, f = map(int, parts)
        except ValueError as exc:
            raise ValueError("Invalid timecode format. Use HH:MM:SS:FF") from exc

        if not 0 <= h or not 0 <= m < 60 or not 0 <= s < 60 or not 0 <= f < frames_per_second:
            raise ValueError("Invalid timecode value")
        if drop_frame and m % 10 != 0 and s == 0 and f < 2:
            raise ValueError("Frames 00 and 01 are skipped at the start of each minute")

        total_minutes = h * 60 + m
        total_frames = (
            (h * 3600 + m * 60 + s) * frames_per_second + f
        )
        if drop_frame:
            total_frames -= 2 * (total_minutes - total_minutes // 10)
        return sign * total_frames

    @classmethod
    def _frames_to_timecode(cls, frames, frames_per_second, drop_frame):
        """Convert an absolute frame number into a timecode label."""
        sign = "-" if frames < 0 else ""
        frames = abs(int(frames))

        if drop_frame:
            frames_per_10_minutes = 17982
            frames_in_first_minute = 1800
            frames_per_minute = 1798
            ten_minute_blocks, remainder = divmod(frames, frames_per_10_minutes)
            nominal_frames = ten_minute_blocks * 18000

            if remainder < frames_in_first_minute:
                nominal_frames += remainder
            else:
                remainder -= frames_in_first_minute
                minutes, remainder = divmod(remainder, frames_per_minute)
                nominal_frames += (minutes + 1) * 1800 + remainder + 2
        else:
            nominal_frames = frames

        f = nominal_frames % frames_per_second
        total_seconds = nominal_frames // frames_per_second
        s = total_seconds % 60
        total_minutes = total_seconds // 60
        m = total_minutes % 60
        h = total_minutes // 60
        return f"{sign}{h:02d}:{m:02d}:{s:02d}:{f:02d}"

    def convert_to_frames(self):
        """Convert duration to total frames"""
        try:
            duration = self.duration_entry.get()
            fps = float(self.fps_var.get())
            drop_frame = self.format_var.get() == "Drop Frame"
            frames_per_second = self._nominal_fps(fps)
            if drop_frame and fps not in (29.97, 30):
                raise ValueError("Drop frame is available only at 29.97 or 30 fps")

            total_frames = self._timecode_to_frames(duration, frames_per_second, drop_frame)
            self.frames_label.configure(text=f"Frames: {total_frames:,}")

        except ValueError as e:
            self.frames_label.configure(text=f"Frames: {str(e)}")
        except Exception:
            self.frames_label.configure(text="Frames: Invalid input")

    def clear_fields(self):
        """Reset the calculator inputs and displayed results."""
        for entry in (self.tc1_entry, self.tc2_entry, self.duration_entry):
            entry.delete(0, "end")
        self._show_result("--:--:--:--")
        self.frames_label.configure(text="Frames: --")