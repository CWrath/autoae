import json
import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import edl_report_core as core
import music_cue_core as mc
from base_tool import BaseTool
from drag_drop import enable_path_drop
import theme

FPS_CHOICES = ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"]
SOURCE_TYPES = (("EDL, ALE or XML", "*.edl *.ale *.xml *.fcpxml"), ("All files", "*.*"))
TABLE_TYPES = (("Spreadsheet", "*.xlsx *.csv"), ("All files", "*.*"))
DOCX_TYPES = (("Word document", "*.docx"),)
DEFAULT_TEMPLATE = os.path.join(theme.HERE, "assets", "templates", "paramount_cue_sheet.docx")


def _data_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AUTO_AE"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AUTO_AE"


PROFILES_FILE = _data_dir() / "music_cue_profiles.json"
TRACKS_FILE = _data_dir() / "music_library_tracks.json"


def open_in_default_app(path):
    if sys.platform.startswith("win"):
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Music Cue Sheet"
        self.description = "Fill a Word cue sheet from the music tracks of an EDL"
        self.category = "Editorial & Turnover"
        self.icon = "audio-lines"
        self.profiles = {}

    def create_window(self, parent):
        window = self.create_tool_window(parent, "Music Cue Sheet", 940, 900)
        window.minsize(780, 560)
        self.window = window
        self.last_output = None
        self.info_entries = {}

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))

        actions = theme.action_bar(page)
        theme.Button(actions, "Clear", kind="ghost", width=76, command=self.clear_fields).pack(side="left")
        self.open_button = theme.Button(actions, "Open cue sheet", kind="secondary", width=140,
                                        command=self.open_output, state="disabled")
        self.open_button.pack(side="right", padx=(0, 10))
        self.run_button = theme.Button(actions, "Create cue sheet", kind="primary", width=160, command=self.run)
        self.run_button.pack(side="right", padx=(0, 10))

        log_card = theme.Card(page, title="Activity")
        log_card.pack(side="bottom", fill="x", pady=(14, 0))
        self.log_box = theme.LogBox(log_card.body, empty_text="Nothing yet. Choose an EDL and create a cue sheet.",
                                    height=130)
        self.log_box.pack(fill="x")

        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # --- Input ----------------------------------------------------------
        source = theme.Card(scroll, title="Input")
        source.pack(fill="x")
        self.source_field = theme.PathField(source.body, "EDL / ALE / XML", "Choose or drop an EDL, ALE or XML",
                                            command=self.browse_source)
        self.source_field.pack(fill="x", pady=(0, 10))
        self.library_field = theme.PathField(source.body, "Music library",
                                             "Spreadsheet with titles, writers, publishers, PRO, label",
                                             command=self.browse_library)
        self.library_field.pack(fill="x", pady=(0, 10))
        match = ctk.CTkFrame(source.body, fg_color="transparent")
        match.pack(fill="x", pady=(0, 10))
        self.key_var = ctk.StringVar(value="First column")
        self.key_menu = ctk.CTkOptionMenu(match, variable=self.key_var, values=["First column"], width=220)
        theme.form_row(match, 0, "Match on column", self.key_menu, pady=(0, 0), sticky="w")
        theme.muted(match, "Compared with each clip name; file extensions are ignored.").grid(
            row=0, column=2, padx=(14, 0), sticky="w")
        theme.Button(source.body, "Export blank library from EDL…", kind="ghost", width=240,
                     command=self.export_library).pack(anchor="w")

        cache_row = ctk.CTkFrame(source.body, fg_color="transparent")
        cache_row.pack(fill="x", pady=(10, 0))
        theme.Button(cache_row, "Paste track details…", kind="secondary", width=170,
                     command=self.paste_tracks).pack(side="left")
        theme.Button(cache_row, "Search missing in library", kind="ghost", width=190,
                     command=self.search_missing).pack(side="left", padx=(10, 0))
        self.cache_label = theme.muted(cache_row, "")
        self.cache_label.pack(side="left", padx=(14, 0))
        self.missing = []
        self.refresh_cache_label()

        # --- Cue options ----------------------------------------------------
        options = theme.Card(scroll, title="Cues")
        options.pack(fill="x", pady=(14, 0))
        body = options.body
        body.grid_columnconfigure(1, weight=1)
        self.track_entry = ctk.CTkEntry(body, placeholder_text="Music tracks, e.g. A13-A17 or A13, A14", height=34)
        theme.form_row(body, 0, "Music tracks", self.track_entry)
        self.exclude_entry = ctk.CTkEntry(body, placeholder_text="Skip clips containing, e.g. SFX, GFX, SIREN", height=34)
        theme.form_row(body, 1, "Exclude", self.exclude_entry)
        row = ctk.CTkFrame(body, fg_color="transparent")
        self.fps_var = ctk.StringVar(value="23.976")
        ctk.CTkOptionMenu(row, variable=self.fps_var, values=FPS_CHOICES, width=110).pack(side="left")
        theme.muted(row, "EDLs only. ALE and XML files carry their own.").pack(side="left", padx=(12, 0))
        theme.form_row(body, 2, "Frame rate", row, sticky="w")
        use_row = ctk.CTkFrame(body, fg_color="transparent")
        self.use_entry = ctk.CTkEntry(use_row, width=70, height=34)
        self.use_entry.insert(0, "BI")
        self.use_entry.pack(side="left")
        theme.muted(use_row, "Used when the library has no Use Type column (BI, BV, VI, VV, MT, ET).").pack(
            side="left", padx=(12, 0))
        theme.form_row(body, 3, "Default use type", use_row, sticky="w", pady=(0, 0))

        # --- Show info ------------------------------------------------------
        info = theme.Card(scroll, title="Show information")
        info.pack(fill="x", pady=(14, 0))
        profile_row = ctk.CTkFrame(info.body, fg_color="transparent")
        profile_row.pack(fill="x", pady=(0, 10))
        self.profile_var = ctk.StringVar(value="")
        self.profile_menu = ctk.CTkOptionMenu(profile_row, variable=self.profile_var, values=["-"], width=240,
                                              command=self.load_profile)
        self.profile_menu.pack(side="left")
        theme.Button(profile_row, "Save as…", kind="ghost", width=90, command=self.save_profile).pack(
            side="left", padx=(10, 0))
        theme.Button(profile_row, "Delete", kind="ghost", width=70, command=self.delete_profile).pack(side="left")
        grid = ctk.CTkFrame(info.body, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1, uniform="info")
        for index, label in enumerate(mc.INFO_FIELDS + ["Cue sheet ID"]):
            entry = ctk.CTkEntry(grid, placeholder_text=label, height=34)
            entry.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 6) if index % 2 == 0 else (6, 0),
                       pady=(0, 6))
            self.info_entries[label] = entry
        theme.muted(info.body, "Blank Length of Show is filled from the EDL; Last Updated Date defaults to today.").pack(
            anchor="w", pady=(4, 0))

        # --- Output ---------------------------------------------------------
        output = theme.Card(scroll, title="Output")
        output.pack(fill="x", pady=(14, 0))
        self.template_field = theme.PathField(output.body, "Word template", "Blank = built-in Paramount layout",
                                              command=self.browse_template)
        self.template_field.pack(fill="x", pady=(0, 10))
        self.output_field = theme.PathField(output.body, "Save as", "Leave blank to save next to the EDL",
                                            command=self.browse_output, button_text="Save as…")
        self.output_field.pack(fill="x")

        enable_path_drop(window, self.source_field.entry, self.drop_source, source)
        enable_path_drop(window, self.library_field.entry, self.drop_library)
        enable_path_drop(window, self.template_field.entry, self.drop_template)

        self.reload_profiles()
        return window

    # --- helpers --------------------------------------------------------------
    def log(self, text):
        self.log_box.insert(tk.END, text)

    @staticmethod
    def set_entry(entry, value):
        entry.delete(0, tk.END)
        entry.insert(0, str(value))

    def info_values(self):
        return {label: entry.get().strip() for label, entry in self.info_entries.items()}

    def reload_profiles(self):
        try:
            self.profiles = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.profiles = {}
        names = list(self.profiles) or ["-"]
        self.profile_menu.configure(values=names)
        self.profile_var.set("-" if not self.profiles else self.profile_var.get() if self.profile_var.get() in self.profiles else "-")

    def write_profiles(self):
        PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROFILES_FILE.write_text(json.dumps(self.profiles, indent=2), encoding="utf-8")

    def load_profile(self, name):
        values = self.profiles.get(name)
        if not values:
            return
        for label, entry in self.info_entries.items():
            self.set_entry(entry, values.get(label, ""))

    def save_profile(self):
        name = ctk.CTkInputDialog(text="Name for this show's details:", title="Save show").get_input()
        if not name or not name.strip():
            return
        self.profiles[name.strip()] = self.info_values()
        self.write_profiles()
        self.reload_profiles()
        self.profile_var.set(name.strip())
        self.log("Saved show details as '{}'.".format(name.strip()))

    def delete_profile(self):
        name = self.profile_var.get()
        if name in self.profiles and messagebox.askyesno("Delete", "Delete the saved show '{}'?".format(name)):
            del self.profiles[name]
            self.write_profiles()
            self.reload_profiles()

    # --- inputs ---------------------------------------------------------------
    def browse_source(self):
        path = filedialog.askopenfilename(title="Choose an EDL, ALE or XML", filetypes=SOURCE_TYPES)
        if path:
            self.load_source(path)

    def browse_library(self):
        path = filedialog.askopenfilename(title="Choose the music library spreadsheet", filetypes=TABLE_TYPES)
        if path:
            self.load_library(path)

    def browse_template(self):
        path = filedialog.askopenfilename(title="Choose a Word template", filetypes=DOCX_TYPES)
        if path:
            self.set_entry(self.template_field.entry, path)

    def browse_output(self):
        path = filedialog.asksaveasfilename(title="Save cue sheet as", defaultextension=".docx", filetypes=DOCX_TYPES)
        if path:
            self.set_entry(self.output_field.entry, path)

    def drop_source(self, paths):
        path = next((p for p in paths if os.path.isfile(p)), None)
        if path:
            self.load_source(path)

    def drop_library(self, paths):
        path = next((p for p in paths if os.path.isfile(p)), None)
        if path:
            self.load_library(path)

    def drop_template(self, paths):
        path = next((p for p in paths if p.lower().endswith(".docx")), None)
        if path:
            self.set_entry(self.template_field.entry, path)
        else:
            self.log("Warning: a template must be a .docx file.")

    def load_source(self, path):
        """Show which tracks the EDL has so the music tracks are easy to pick."""
        self.set_entry(self.source_field.entry, path)
        try:
            events = core.parse_file(path, float(self.fps_var.get()))[0]
        except Exception as error:  # a malformed file shouldn't take the window down
            self.log("Error: couldn't read {}: {}".format(Path(path).name, error))
            return
        self.log("Found {} events in {}. Tracks:".format(len(events), Path(path).name))
        for track, count, sample in sorted(mc.track_summary(events), key=lambda t: str(t[0])):
            self.log("  {}: {} events, e.g. {}".format(track or "?", count, sample or "(no name)"))

    def load_library(self, path):
        self.set_entry(self.library_field.entry, path)
        try:
            headers, records = core.read_table(path)
        except Exception as error:  # unreadable or locked spreadsheet
            self.log("Error: couldn't read {}: {}".format(Path(path).name, error))
            return
        headers = [h for h in headers if h]
        self.key_menu.configure(values=headers or ["First column"])
        self.key_var.set(headers[0] if headers else "First column")
        self.log("Found {} rows and {} columns in {}.".format(len(records), len(headers), Path(path).name))

    def export_library(self):
        source = self.source_field.get().strip()
        if not os.path.isfile(source):
            messagebox.showwarning("Music Cue Sheet", "Choose an EDL first.")
            return
        path = filedialog.asksaveasfilename(title="Save blank music library", defaultextension=".xlsx",
                                            initialfile=Path(source).stem + " music library.xlsx",
                                            filetypes=(("Excel", "*.xlsx"),))
        if not path:
            return
        try:
            count = mc.write_library_template(source, path, float(self.fps_var.get()), self.track_entry.get(),
                                          self.exclude_entry.get())
        except PermissionError:
            self.log("Error: {} is open in another program.".format(Path(path).name))
            return
        except Exception as error:
            self.log("Error: {}".format(error))
            return
        self.log("Processed {} music clips into {}. Fill it in, then choose it as the Music library.".format(
            count, Path(path).name))

    # --- library tracks -------------------------------------------------------
    def refresh_cache_label(self):
        self.cache_label.configure(text="{} tracks saved from the library".format(len(mc.load_cache(TRACKS_FILE))))

    def paste_tracks(self):
        dialog = ctk.CTkToplevel(self.window)
        dialog.title("Paste track details")
        dialog.geometry("640x460")
        dialog.transient(self.window)
        theme.muted(dialog, "On a track page in the music library, select all, copy, and paste here. "
                            "Paste several tracks at once if you like.").pack(anchor="w", padx=16, pady=(14, 6))
        box = ctk.CTkTextbox(dialog)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        def add():
            records = mc.parse_track_pages(box.get("1.0", tk.END))
            if not records:
                self.log("Warning: no track details found. The page needs a 'Filename:' field.")
                return
            mc.add_to_cache(TRACKS_FILE, records)
            for record in records:
                self.log("Processed library track: {} ({})".format(record["Filename"], record["Writer(s)"] or "no writers"))
            self.refresh_cache_label()
            dialog.destroy()

        theme.Button(dialog, "Add to library", kind="primary", width=140, command=add).pack(
            anchor="e", padx=16, pady=(0, 14))
        dialog.after(100, box.focus_set)

    def search_missing(self):
        if not self.missing:
            self.log("Nothing missing. Create a cue sheet first; songs without a library entry are searched.")
            return
        if len(self.missing) > 6 and not messagebox.askyesno(
                "Search library", "Open {} browser tabs, one per missing song?".format(len(self.missing))):
            return
        for song in self.missing:
            webbrowser.open(mc.search_url(song))
        self.log("Started: searching the library for {} songs.".format(len(self.missing)))

    # --- run ------------------------------------------------------------------
    def clear_fields(self):
        for entry in [self.source_field.entry, self.library_field.entry, self.template_field.entry,
                      self.output_field.entry, self.track_entry, self.exclude_entry, *self.info_entries.values()]:
            entry.delete(0, tk.END)
        self.key_menu.configure(values=["First column"])
        self.key_var.set("First column")
        self.profile_var.set("-")
        self.last_output = None
        self.open_button.configure(state="disabled")

    def open_output(self):
        if self.last_output and os.path.exists(self.last_output):
            open_in_default_app(self.last_output)

    def run(self):
        source = self.source_field.get().strip()
        if not os.path.isfile(source):
            messagebox.showwarning("Music Cue Sheet", "Choose an EDL, ALE or XML file first.")
            return
        library = self.library_field.get().strip()
        if library and not os.path.isfile(library):
            messagebox.showwarning("Music Cue Sheet", "The music library file can't be found.")
            return
        template = self.template_field.get().strip() or DEFAULT_TEMPLATE
        if not os.path.isfile(template):
            messagebox.showwarning("Music Cue Sheet", "The Word template can't be found.")
            return
        output = self.output_field.get().strip() or str(Path(source).with_name(Path(source).stem + " - Cue Sheet.docx"))
        key = self.key_var.get()
        values = self.info_values()
        sheet_id = values.pop("Cue sheet ID", "")
        info = {label: value for label, value in values.items() if value}

        self.run_button.configure(state="disabled")
        self.missing = []
        try:
            count = mc.build_cue_sheet(
                source, template, output, fps=float(self.fps_var.get()), track_filter=self.track_entry.get(),
                exclude=self.exclude_entry.get(), library_path=library,
                key_column="" if key == "First column" else key, info=info,
                cache_records=mc.load_cache(TRACKS_FILE), missing=self.missing,
                sheet_id=sheet_id, default_use=self.use_entry.get().strip().upper() or "BI", log=self.log)
        except PermissionError:
            self.log("Error: {} is open in another program. Close it and try again.".format(Path(output).name))
            return
        except (ValueError, OSError, KeyError) as error:
            self.log("Error: {}".format(error))
            messagebox.showerror("Music Cue Sheet", str(error))
            return
        except Exception as error:  # a malformed EDL or template shouldn't take the window down
            self.log("Error: couldn't create the cue sheet: {}".format(error))
            return
        finally:
            self.run_button.configure(state="normal")

        self.last_output = output
        self.open_button.configure(state="normal")
        self.log("Done: {} cues written.".format(count))
