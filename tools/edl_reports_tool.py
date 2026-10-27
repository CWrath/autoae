import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import edl_report_core as core
from base_tool import BaseTool
from drag_drop import enable_path_drop
import theme

FPS_CHOICES = ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"]
SOURCE_TYPES = (("EDL, ALE or XML", "*.edl *.ale *.xml *.fcpxml"), ("All files", "*.*"))
TABLE_TYPES = (("Spreadsheet", "*.xlsx *.csv"), ("All files", "*.*"))


def _data_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AUTO_AE"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AUTO_AE"


PRESETS_FILE = _data_dir() / "edl_report_presets.json"


def open_in_default_app(path):
    if sys.platform.startswith("win"):
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def ensure_presets_file():
    """Create the user presets file with a copy of the clearance preset to start from."""
    if not PRESETS_FILE.exists():
        PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        example = json.loads(json.dumps(core.BUILTIN_PRESETS["Clearance Report"]))
        example["title"] = "{project}  |  My Client Clearance Report"
        PRESETS_FILE.write_text(json.dumps({"My Client Clearance": example}, indent=2), encoding="utf-8")
    return PRESETS_FILE


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "EDL Reports"
        self.description = "Turn EDL, ALE or XML into cue sheets, timing sheets and clearance reports"
        self.category = "Editorial & Turnover"
        self.icon = "list-video"
        self.presets = {}

    def create_window(self, parent):
        window = self.create_tool_window(parent, "EDL Reports", 940, 900)
        window.minsize(780, 560)
        self.window = window
        self.last_output = None

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))

        actions = theme.action_bar(page)
        theme.Button(actions, "Clear", kind="ghost", width=76, command=self.clear_fields).pack(side="left")
        self.open_button = theme.Button(actions, "Open report", kind="secondary", width=120,
                                        command=self.open_output, state="disabled")
        self.open_button.pack(side="right", padx=(0, 10))
        self.run_button = theme.Button(actions, "Create report", kind="primary", width=150, command=self.run)
        self.run_button.pack(side="right", padx=(0, 10))

        # The log stays pinned under the form; the form scrolls when the window is short.
        log_card = theme.Card(page, title="Activity")
        log_card.pack(side="bottom", fill="x", pady=(14, 0))
        self.log_box = theme.LogBox(log_card.body, empty_text="Nothing yet. Choose a file and create a report.", height=130)
        self.log_box.pack(fill="x")

        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # --- Input ----------------------------------------------------------
        source = theme.Card(scroll, title="Input")
        source.pack(fill="x")
        self.source_field = theme.PathField(source.body, "EDL / ALE / XML", "Choose or drop an EDL, ALE or XML",
                                            command=self.browse_source)
        self.source_field.pack(fill="x", pady=(0, 10))
        self.meta_field = theme.PathField(source.body, "Cue data (optional)",
                                          "Spreadsheet with composer, licensor, status…",
                                          command=self.browse_metadata)
        self.meta_field.pack(fill="x", pady=(0, 10))
        match = ctk.CTkFrame(source.body, fg_color="transparent")
        match.pack(fill="x")
        theme.form_row(match, 0, "Match on column", self._make_key_menu(match), pady=(0, 0), sticky="w")
        theme.muted(match, "Compared with each clip name; file extensions are ignored.").grid(
            row=0, column=2, padx=(14, 0), sticky="w")

        # --- Report ---------------------------------------------------------
        report = theme.Card(scroll, title="Report")
        report.pack(fill="x", pady=(14, 0))
        body = report.body
        body.grid_columnconfigure(1, weight=1)

        preset_row = ctk.CTkFrame(body, fg_color="transparent")
        self.preset_var = ctk.StringVar()
        self.preset_menu = ctk.CTkOptionMenu(preset_row, variable=self.preset_var, values=["-"], width=240,
                                             command=self.on_preset_change)
        self.preset_menu.pack(side="left")
        theme.Button(preset_row, "Edit presets…", kind="ghost", width=112, command=self.edit_presets).pack(
            side="left", padx=(10, 0))
        theme.Button(preset_row, "Reload", kind="ghost", width=70, command=self.reload_presets).pack(side="left")
        theme.form_row(body, 0, "Style", preset_row, sticky="w")

        self.project_entry = ctk.CTkEntry(body, placeholder_text="Shown in the report title", height=34)
        theme.form_row(body, 1, "Project name", self.project_entry)

        names = ctk.CTkFrame(body, fg_color="transparent")
        names.grid_columnconfigure((0, 1), weight=1, uniform="names")
        self.series_entry = ctk.CTkEntry(names, placeholder_text="Series title", height=34)
        self.series_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
        self.episode_title_entry = ctk.CTkEntry(names, placeholder_text="Episode title", height=34)
        self.episode_title_entry.grid(row=0, column=1, sticky="ew", pady=(0, 6))
        self.episode_number_entry = ctk.CTkEntry(names, placeholder_text="Episode number", height=34)
        self.episode_number_entry.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self.program_id_entry = ctk.CTkEntry(names, placeholder_text="Program ID (blank = N/A)", height=34)
        self.program_id_entry.grid(row=1, column=1, sticky="ew")
        theme.form_row(body, 2, "Series / episode", names, pady=(0, 10))

        options = ctk.CTkFrame(body, fg_color="transparent")
        self.fps_var = ctk.StringVar(value="23.976")
        ctk.CTkOptionMenu(options, variable=self.fps_var, values=FPS_CHOICES, width=110).pack(side="left")
        theme.muted(options, "EDLs only. ALE and XML files carry their own.").pack(side="left", padx=(12, 0))
        theme.form_row(body, 3, "Frame rate", options, sticky="w")

        self.track_entry = ctk.CTkEntry(body, placeholder_text="Blank = every track. e.g. A*  or  A, A2", height=34)
        theme.form_row(body, 4, "Tracks to include", self.track_entry)
        break_row = ctk.CTkFrame(body, fg_color="transparent")
        self.break_entry = ctk.CTkEntry(break_row, width=70, height=34)
        self.break_entry.insert(0, "2")
        self.break_entry.pack(side="left")
        theme.muted(break_row, "seconds. Timing sheet only: other black gaps stay inside the segment. Blank = every gap.").pack(
            side="left", padx=(12, 0))
        theme.form_row(body, 5, "Break length", break_row, sticky="w")
        deduct_row = ctk.CTkFrame(body, fg_color="transparent")
        self.deduct_entry = ctk.CTkEntry(deduct_row, width=70, height=34)
        self.deduct_entry.insert(0, "2")
        self.deduct_entry.pack(side="left")
        theme.muted(deduct_row, "seconds taken off the 29.97/DF total, e.g. a credits card that isn't counted.").pack(
            side="left", padx=(12, 0))
        theme.form_row(body, 6, "Uncounted card", deduct_row, sticky="w")

        checks = ctk.CTkFrame(body, fg_color="transparent")
        checks.grid(row=7, column=1, sticky="w")
        self.merge_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(checks, text="Combine back-to-back cuts of the same clip",
                        variable=self.merge_var).pack(side="left", padx=(0, 24))
        self.gaps_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(checks, text="Keep black / slug events", variable=self.gaps_var).pack(side="left")

        # --- Output ---------------------------------------------------------
        output = theme.Card(scroll, title="Output")
        output.pack(fill="x", pady=(14, 0))
        self.template_field = theme.PathField(output.body, "Client template", "Optional: fill the client's own .xlsx instead",
                                              command=self.browse_template)
        self.template_field.pack(fill="x", pady=(0, 10))
        self.output_field = theme.PathField(output.body, "Save as", "Leave blank to save next to the EDL",
                                            command=self.browse_output, button_text="Save as…")
        self.output_field.pack(fill="x")

        enable_path_drop(window, self.source_field.entry, self.drop_source, source)
        enable_path_drop(window, self.meta_field.entry, self.drop_metadata)
        enable_path_drop(window, self.template_field.entry, self.drop_template)

        self.reload_presets()
        return window

    # --- helpers --------------------------------------------------------------
    def _make_key_menu(self, master):
        self.key_var = ctk.StringVar(value="First column")
        self.key_menu = ctk.CTkOptionMenu(master, variable=self.key_var, values=["First column"], width=220)
        return self.key_menu

    def log(self, text):
        self.log_box.insert(tk.END, text)

    @staticmethod
    def set_entry(entry, value):
        entry.delete(0, tk.END)
        entry.insert(0, str(value))

    def reload_presets(self):
        self.presets = core.all_presets(PRESETS_FILE)
        names = list(self.presets)
        self.preset_menu.configure(values=names)
        current = self.preset_var.get()
        self.preset_var.set(current if current in self.presets else names[0])
        self.on_preset_change(self.preset_var.get())

    def on_preset_change(self, name):
        preset = self.presets.get(name, {})
        self.merge_var.set(bool(preset.get("merge_back_to_back", False)))

    def edit_presets(self):
        try:
            path = ensure_presets_file()
            open_in_default_app(path)
            self.log("Started: editing {}. Save it, then press Reload.".format(path))
        except OSError as error:
            messagebox.showerror("Presets", "Couldn't open the presets file:\n{}".format(error))

    # --- inputs ---------------------------------------------------------------
    def browse_source(self):
        path = filedialog.askopenfilename(title="Choose an EDL, ALE or XML", filetypes=SOURCE_TYPES)
        if path:
            self.set_entry(self.source_field.entry, path)

    def browse_metadata(self):
        path = filedialog.askopenfilename(title="Choose the cue data spreadsheet", filetypes=TABLE_TYPES)
        if path:
            self.load_metadata(path)

    def browse_template(self):
        path = filedialog.askopenfilename(title="Choose the client's Excel template",
                                          filetypes=(("Excel", "*.xlsx *.xlsm"),))
        if path:
            self.set_entry(self.template_field.entry, path)

    def browse_output(self):
        path = filedialog.asksaveasfilename(title="Save report as", defaultextension=".xlsx",
                                            filetypes=(("Excel", "*.xlsx"),))
        if path:
            self.set_entry(self.output_field.entry, path)

    def drop_source(self, paths):
        path = next((p for p in paths if os.path.isfile(p)), None)
        if path:
            self.set_entry(self.source_field.entry, path)

    def drop_metadata(self, paths):
        path = next((p for p in paths if os.path.isfile(p)), None)
        if path:
            self.load_metadata(path)

    def drop_template(self, paths):
        path = next((p for p in paths if p.lower().endswith((".xlsx", ".xlsm"))), None)
        if path:
            self.set_entry(self.template_field.entry, path)
        else:
            self.log("Warning: a client template must be an .xlsx file.")

    def load_metadata(self, path):
        self.set_entry(self.meta_field.entry, path)
        try:
            headers, records = core.read_table(path)
        except Exception as error:  # unreadable or locked spreadsheet
            self.log("Error: couldn't read {}: {}".format(Path(path).name, error))
            return
        headers = [h for h in headers if h]
        self.key_menu.configure(values=headers or ["First column"])
        self.key_var.set(headers[0] if headers else "First column")
        self.log("Found {} rows and {} columns in {}.".format(len(records), len(headers), Path(path).name))

    # --- run ------------------------------------------------------------------
    def clear_fields(self):
        for entry in (self.source_field.entry, self.meta_field.entry, self.template_field.entry,
                      self.output_field.entry, self.project_entry, self.track_entry, self.series_entry,
                      self.episode_title_entry, self.episode_number_entry, self.program_id_entry):
            entry.delete(0, tk.END)
        self.key_menu.configure(values=["First column"])
        self.key_var.set("First column")
        self.last_output = None
        self.open_button.configure(state="disabled")

    def open_output(self):
        if self.last_output and os.path.exists(self.last_output):
            open_in_default_app(self.last_output)

    def run(self):
        source = self.source_field.get().strip()
        if not os.path.isfile(source):
            messagebox.showwarning("EDL Reports", "Choose an EDL, ALE or XML file first.")
            return
        metadata = self.meta_field.get().strip() or None
        if metadata and not os.path.isfile(metadata):
            messagebox.showwarning("EDL Reports", "The cue data file can't be found.")
            return
        template = self.template_field.get().strip() or None
        if template and not os.path.isfile(template):
            messagebox.showwarning("EDL Reports", "The client template can't be found.")
            return
        preset_name = self.preset_var.get()
        preset = self.presets[preset_name]
        output = self.output_field.get().strip() or str(
            Path(source).with_name("{} - {}.xlsx".format(Path(source).stem, preset_name)))
        key = self.key_var.get()
        try:
            break_seconds = float(self.break_entry.get()) if self.break_entry.get().strip() else None
            deduct_seconds = float(self.deduct_entry.get()) if self.deduct_entry.get().strip() else 0
        except ValueError:
            messagebox.showwarning("EDL Reports", "Break length and uncounted card must be numbers of seconds.")
            return

        self.log("Starting: {} with the {} style.".format(Path(source).name, preset_name))
        try:
            summary = core.build_report(
                source, output, preset, fps=float(self.fps_var.get()), project=self.project_entry.get().strip(),
                track_filter=self.track_entry.get(), metadata_path=metadata, key_column=key,
                merge=self.merge_var.get(), template_path=template, include_gaps=self.gaps_var.get(), break_seconds=break_seconds,
                df_deduct_seconds=deduct_seconds,
                fields={"series": self.series_entry.get().strip(),
                        "episode_title": self.episode_title_entry.get().strip(),
                        "episode_number": self.episode_number_entry.get().strip(),
                        "program_id": self.program_id_entry.get().strip()})
        except PermissionError:
            self.log("Error: {} is open in another program. Close it and try again.".format(Path(output).name))
            return
        except (ValueError, OSError, KeyError) as error:
            self.log("Error: {}".format(error))
            messagebox.showerror("EDL Reports", str(error))
            return
        except Exception as error:  # a malformed EDL/XML shouldn't take the window down
            self.log("Error: couldn't read {}: {}".format(Path(source).name, error))
            return

        self.report(summary, output)

    def report(self, summary, output):
        if "segments" in summary:
            self.log("Found {} segments and {} breaks; program runs {}.".format(
                summary["segments"], summary["breaks"],
                core.frames_to_tc(summary["runtime_frames"], summary["fps"])))
            self.log("Done: {}".format(output))
            self.last_output = output
            self.open_button.configure(state="normal")
            return
        self.log("Found {} events; {} lines in the report, {} runtime.".format(
            summary["parsed"], summary["written"],
            core.frames_to_tc(summary["runtime_frames"], summary["fps"])))
        if summary["matched"] is not None:
            self.log("Processed: {} of {} lines matched your cue data.".format(summary["matched"], summary["written"]))
            for name in summary["unmatched"]:
                self.log("Warning: no cue data for {}".format(name))
            for key in summary["unused"][:20]:
                self.log("Skipped: cue data row not used in this EDL: {}".format(key))
        if summary["template_columns"] is not None:
            self.log("Processed: filled template columns {}.".format(", ".join(str(c) for c in summary["template_columns"])))
        if summary["written"] == 0:
            self.log("Warning: nothing matched the track filter, so the report is empty.")
        self.log("Done: {}".format(output))
        self.last_output = output
        self.open_button.configure(state="normal")
