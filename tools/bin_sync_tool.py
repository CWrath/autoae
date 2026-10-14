import shutil
import json
import os
import sys
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

from base_tool import BaseTool
import theme
from theme import C


def _data_dir():
    """Folder for Bin Sync's recent paths and presets (Application Support on a Mac)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AUTO_AE"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AUTO_AE"


RECENT_PATHS_FILE = _data_dir() / "bin_sync_recent.json"
PRESETS_FILE = _data_dir() / "bin_sync_presets.json"
MAX_RECENT_PATHS = 10


def load_recent_paths():
    """Load previously used source and destination paths."""
    if not RECENT_PATHS_FILE.is_file():
        return {"sources": [], "destinations": []}

    try:
        with RECENT_PATHS_FILE.open("r", encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"sources": [], "destinations": []}

    return {
        "sources": [path for path in saved.get("sources", []) if Path(path).is_file()],
        "destinations": [
            path for path in saved.get("destinations", []) if Path(path).is_dir()
        ],
    }


def save_recent_paths(recent_paths):
    """Persist recently used paths outside the project directory."""
    RECENT_PATHS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RECENT_PATHS_FILE.open("w", encoding="utf-8") as file:
        json.dump(recent_paths, file, indent=2)


def add_recent_path(recent_paths, category, path):
    """Add a path to the front of its recent list and persist it."""
    path_string = str(Path(path).resolve())
    paths = [item for item in recent_paths[category] if item != path_string]
    recent_paths[category] = [path_string] + paths[: MAX_RECENT_PATHS - 1]
    save_recent_paths(recent_paths)


def load_presets():
    """Load saved named groups of destination folders."""
    if not PRESETS_FILE.is_file():
        return {}

    try:
        with PRESETS_FILE.open("r", encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(saved, dict):
        return {}

    presets = {}
    for name, paths in saved.items():
        if isinstance(name, str) and isinstance(paths, list):
            valid_paths = [
                path for path in paths if isinstance(path, str) and Path(path).is_dir()
            ]
            if valid_paths:
                presets[name] = valid_paths
    return presets


def save_presets(presets):
    """Persist named destination groups outside the project directory."""
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PRESETS_FILE.open("w", encoding="utf-8") as file:
        json.dump(presets, file, indent=2)


def bin_lock_path(bin_path):
    """Return the Avid lock-file path associated with a bin."""
    return Path(bin_path).with_suffix(".lck")


def sync_bin(source_path, destination_paths, overwrite=True):
    """Copy an Avid bin to each destination directory.

    Returns a list of (destination, error) pairs. A None error means the
    destination was copied successfully.
    """
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Bin file not found: {source}")
    if source.suffix.lower() != ".avb":
        raise ValueError("The selected file must have an .avb extension.")

    results = []
    source_resolved = source.resolve()
    for destination_path in destination_paths:
        destination = Path(destination_path)
        try:
            if not destination.is_dir():
                raise NotADirectoryError(f"Destination folder not found: {destination}")

            target = destination / source.name
            if target.resolve() == source_resolved:
                raise ValueError("Destination is the folder containing the source bin.")
            lock_path = bin_lock_path(target)
            if lock_path.is_file():
                raise PermissionError(f"Bin lock exists: {lock_path}")
            if target.exists() and not overwrite:
                raise FileExistsError(f"{target.name} already exists")

            shutil.copy2(source, target)
            results.append((destination, None))
        except (FileExistsError, OSError, ValueError) as error:
            results.append((destination, error))

    return results


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Bin Sync"
        self.description = "Copy one Avid .avb bin to multiple locations"
        self.category = "Editorial & Turnover"
        self.icon = "folder-sync"
        self.source_path = None
        self.destination_paths = []
        self.recent_paths = load_recent_paths()
        self.presets = load_presets()

    def create_window(self, parent):
        window = self.create_tool_window(parent, "Bin Sync", 780, 690)
        window.minsize(680, 600)

        page = theme.page(window)
        theme.tool_header(
            page, self,
            subtitle="Choose one .avb bin, then add every folder that should receive a copy."
        ).pack(fill="x", pady=(0, 18))

        # Actions first so they stay visible when the window is short
        options_frame = theme.action_bar(page)
        self.overwrite_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            options_frame,
            text="Overwrite existing bins",
            variable=self.overwrite_var,
            checkbox_width=18,
            checkbox_height=18,
        ).pack(side="left")
        theme.Button(
            options_frame, "Sync Bin", kind="primary", width=130, height=38,
            command=self.perform_sync,
        ).pack(side="right")

        # --- Source bin ------------------------------------------------------
        source_card = theme.Card(page, title="Source bin")
        source_card.pack(fill="x")
        source_frame = source_card.body
        source_frame.grid_columnconfigure(1, weight=1)

        path_box = ctk.CTkFrame(source_frame, fg_color=C["field"], border_width=1,
                                border_color=C["border_strong"], corner_radius=8, height=34)
        path_box.grid(row=0, column=0, columnspan=2, sticky="ew")
        path_box.pack_propagate(False)
        self.source_label = ctk.CTkLabel(path_box, text="No bin selected", anchor="w", height=30)
        self.source_label.pack(fill="x", padx=10, pady=2)
        theme.Button(
            source_frame, "Choose .avb Bin…", kind="secondary", width=150, command=self.choose_source
        ).grid(row=0, column=2, padx=(8, 0))

        theme.muted(source_frame, "Recent", role="body", width=70).grid(
            row=1, column=0, sticky="w", pady=(10, 0))
        self.recent_source_menu = ctk.CTkOptionMenu(
            source_frame,
            values=self.recent_source_values(),
            command=self.select_recent_source,
            dynamic_resizing=False,
            height=34,
        )
        self.recent_source_menu.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(10, 0))

        # --- Destinations ----------------------------------------------------
        destinations_card = theme.Card(page, title="Destination folders")
        destinations_card.pack(fill="both", expand=True, pady=(14, 0))
        destinations_frame = destinations_card.body
        destinations_frame.grid_columnconfigure(1, weight=1)
        destinations_frame.grid_rowconfigure(1, weight=1)

        theme.muted(destinations_frame, "Preset", role="body", width=70).grid(row=0, column=0, sticky="w")
        self.preset_menu = ctk.CTkOptionMenu(
            destinations_frame,
            values=self.preset_values(),
            command=self.load_preset,
            dynamic_resizing=False,
            height=34,
        )
        self.preset_menu.grid(row=0, column=1, sticky="ew")
        preset_buttons = ctk.CTkFrame(destinations_frame, fg_color="transparent")
        preset_buttons.grid(row=0, column=2, padx=(8, 0))
        theme.Button(
            preset_buttons, "Save Current…", kind="secondary", width=120, command=self.save_current_preset
        ).pack(side="left")
        theme.Button(
            preset_buttons, "Delete", kind="danger", width=76, command=self.delete_selected_preset
        ).pack(side="left", padx=(8, 0))

        self.destination_list = theme.ThemedListbox(
            destinations_frame,
            height=7,
            selectmode=tk.SINGLE,
            exportselection=False,
        )
        self.destination_list.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=10)

        destination_buttons = ctk.CTkFrame(destinations_frame, fg_color="transparent")
        destination_buttons.grid(row=2, column=0, columnspan=3, sticky="ew")
        theme.Button(
            destination_buttons, "Add Folder…", kind="secondary", width=110, command=self.add_destination,
        ).pack(side="left")
        theme.Button(
            destination_buttons, "Remove Selected", kind="ghost", width=130,
            command=self.remove_selected_destination,
        ).pack(side="left", padx=(8, 0))
        theme.Button(
            destination_buttons, "Clear All", kind="ghost", width=86, command=self.clear_destinations,
        ).pack(side="left")
        self.recent_destination_menu = ctk.CTkOptionMenu(
            destination_buttons,
            values=self.recent_destination_values(),
            command=self.select_recent_destination,
            dynamic_resizing=False,
            width=220,
            height=34,
        )
        self.recent_destination_menu.pack(side="right")
        theme.muted(destination_buttons, "Add recent", role="body").pack(side="right", padx=(0, 8))

        self.window = window
        return window

    def choose_source(self):
        selected = filedialog.askopenfilename(
            title="Choose Avid bin",
            filetypes=[("Avid bins", "*.avb"), ("All files", "*.*")],
        )
        if selected:
            self.source_path = Path(selected)
            self.source_label.configure(text=str(self.source_path))
            add_recent_path(self.recent_paths, "sources", self.source_path)
            self.refresh_recent_menus()

    def select_recent_source(self, selected):
        if selected == "No recent bins":
            return
        self.source_path = Path(selected)
        self.source_label.configure(text=str(self.source_path))

    def select_recent_destination(self, selected):
        if selected == "No recent destinations":
            return
        destination = Path(selected)
        if destination not in self.destination_paths:
            self.destination_paths.append(destination)
            self.refresh_destinations()

    def load_preset(self, selected):
        if selected == "No saved presets":
            return

        destinations = self.presets.get(selected)
        if destinations is None:
            messagebox.showerror("Unable to load preset", f"Preset not found: {selected}")
            return

        self.destination_paths = [Path(path) for path in destinations]
        self.refresh_destinations()

    def save_current_preset(self):
        if not self.destination_paths:
            messagebox.showwarning(
                "No destinations selected",
                "Add at least one destination folder before saving a preset.",
            )
            return

        name = ctk.CTkInputDialog(
            text="Enter a name for this destination preset:", title="Save preset"
        ).get_input()
        if name is None:
            return

        name = name.strip()
        if not name:
            messagebox.showwarning("Invalid preset name", "Enter a name for the preset.")
            return

        self.presets[name] = [str(Path(path).resolve()) for path in self.destination_paths]
        try:
            save_presets(self.presets)
        except OSError as error:
            messagebox.showerror("Unable to save preset", str(error))
            return
        self.refresh_preset_menu()
        self.preset_menu.set(name)

    def delete_selected_preset(self):
        selected = self.preset_menu.get()
        if selected == "No saved presets":
            return
        if selected not in self.presets:
            messagebox.showerror("Unable to delete preset", f"Preset not found: {selected}")
            return
        if not messagebox.askyesno(
            "Delete preset", f'Delete the "{selected}" destination preset?'
        ):
            return

        del self.presets[selected]
        try:
            save_presets(self.presets)
        except OSError as error:
            messagebox.showerror("Unable to delete preset", str(error))
            return
        self.refresh_preset_menu()

    def add_destination(self):
        selected = filedialog.askdirectory(title="Choose bin destination folder")
        if not selected:
            return

        destination = Path(selected).resolve()
        if destination not in self.destination_paths:
            self.destination_paths.append(destination)
            add_recent_path(self.recent_paths, "destinations", destination)
            self.refresh_destinations()
            self.refresh_recent_menus()

    def remove_selected_destination(self):
        selection = self.destination_list.curselection()
        if not selection:
            return

        del self.destination_paths[selection[0]]
        self.refresh_destinations()

    def clear_destinations(self):
        self.destination_paths.clear()
        self.refresh_destinations()

    def refresh_destinations(self):
        self.destination_list.delete(0, tk.END)
        for destination in self.destination_paths:
            self.destination_list.insert(tk.END, str(destination))

    def recent_source_values(self):
        return self.recent_paths["sources"] or ["No recent bins"]

    def recent_destination_values(self):
        return self.recent_paths["destinations"] or ["No recent destinations"]

    def preset_values(self):
        return sorted(self.presets) or ["No saved presets"]

    def refresh_recent_menus(self):
        self.recent_source_menu.configure(values=self.recent_source_values())
        self.recent_destination_menu.configure(values=self.recent_destination_values())

    def refresh_preset_menu(self):
        self.preset_menu.configure(values=self.preset_values())
        if self.presets:
            self.preset_menu.set(sorted(self.presets)[0])
        else:
            self.preset_menu.set("No saved presets")

    def perform_sync(self):
        if self.source_path is None:
            messagebox.showwarning("No bin selected", "Choose an .avb bin first.")
            return
        if not self.destination_paths:
            messagebox.showwarning(
                "No destinations selected",
                "Add at least one destination folder first.",
            )
            return

        destinations = []
        skipped = []
        for destination in self.destination_paths:
            target = Path(destination) / self.source_path.name
            lock_path = bin_lock_path(target)
            if not lock_path.is_file():
                destinations.append(destination)
                continue

            remove_lock = messagebox.askyesno(
                "Avid bin is locked",
                f"A lock file exists for {target.name} in:\n{destination}\n\n"
                "Delete the lock file and continue syncing to this location?",
            )
            if not remove_lock:
                skipped.append(
                    (destination, f"Bin lock was kept: {lock_path}")
                )
                continue

            try:
                lock_path.unlink()
            except OSError as error:
                skipped.append((destination, f"Unable to delete bin lock: {error}"))
                continue
            destinations.append(destination)

        if not destinations and skipped:
            details = "\n".join(f"- {destination}: {error}" for destination, error in skipped)
            messagebox.showwarning(
                "Bin sync cancelled",
                f"No destinations were available for syncing.\n\n{details}",
            )
            return

        try:
            results = sync_bin(
                self.source_path,
                destinations,
                overwrite=self.overwrite_var.get(),
            )
        except (FileNotFoundError, ValueError) as error:
            messagebox.showerror("Unable to sync bin", str(error))
            return

        results.extend((destination, RuntimeError(error)) for destination, error in skipped)
        successful = [destination for destination, error in results if error is None]
        failed = [(destination, error) for destination, error in results if error]
        message = f"Synced {self.source_path.name} to {len(successful)} location(s)."
        if failed:
            details = "\n".join(f"- {destination}: {error}" for destination, error in failed)
            messagebox.showwarning(
                "Bin sync completed with errors",
                f"{message}\n\nFailed locations:\n{details}",
            )
        else:
            messagebox.showinfo("Bin sync complete", message)
