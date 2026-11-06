import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import theme
from base_tool import BaseTool
from drag_drop import enable_path_drop
from theme import C

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(APP_DIR, "installers_settings.json")
KIT_SETTINGS_FILE = os.path.join(APP_DIR, "setup_kit_settings.json")
DEFAULT_LIBRARY = os.path.join(APP_DIR, "installers_library")

EXTENSIONS = {"windows": (".exe", ".msi", ".zip"), "mac": (".dmg", ".pkg", ".zip")}
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}
HERE_OS = "mac" if sys.platform == "darwin" else "windows"


def size_text(num):
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024


def unique_path(folder, name):
    target = Path(folder) / name
    stem, suffix, n = target.stem, target.suffix, 2
    while target.exists():
        target = Path(folder) / f"{stem} ({n}){suffix}"
        n += 1
    return target


def next_kit_number(folder):
    if not Path(folder).is_dir():
        return 1
    numbers = []
    for p in Path(folder).iterdir():
        m = re.match(r"(\d+)_", p.name)
        if m:
            numbers.append(int(m.group(1)))
    return max(numbers, default=0) + 1


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Installers"
        self.description = "Your toolbox of installers (.exe, .msi, .dmg, .pkg) ready to run or deploy"
        self.category = "Project Setup"
        self.icon = "download"

    # --- settings ----------------------------------------------------------
    def load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
                json.dump({"library": self.library}, handle, indent=2)
        except OSError:
            pass

    def kit_folder(self):
        try:
            with open(KIT_SETTINGS_FILE, encoding="utf-8") as handle:
                return json.load(handle).get("kit", "")
        except (OSError, ValueError, AttributeError):
            return ""

    # --- window ------------------------------------------------------------
    def create_window(self, parent):
        window = self.create_tool_window(parent, "Installers", 760, 640)
        window.minsize(620, 520)
        self.window = window
        self.library = self.load_settings().get("library") or DEFAULT_LIBRARY
        self.os_var = ctk.StringVar(value="Mac" if HERE_OS == "mac" else "Windows")
        self.search_var = ctk.StringVar()
        self.checks = {}

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 14))

        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", pady=(0, 10))
        ctk.CTkSegmentedButton(top, values=["Windows", "Mac"], variable=self.os_var,
                               command=lambda _v: self.refresh()).pack(side="left")
        ctk.CTkEntry(top, placeholder_text="Search", textvariable=self.search_var, width=180).pack(side="right")
        self.search_var.trace_add("write", lambda *_: self.refresh())

        actions = theme.action_bar(page, pady=(0, 10))
        theme.Button(actions, "Add Installers", kind="primary", width=130, command=self.add_dialog).pack(side="left")
        theme.Button(actions, "Show Folder", kind="ghost", width=100, command=self.reveal_library).pack(side="left", padx=(6, 0))
        theme.Button(actions, "Change Library", kind="ghost", width=116, command=self.change_library).pack(side="left", padx=(6, 0))
        theme.Button(actions, "Send to Setup Kit", kind="secondary", width=140,
                     command=self.send_to_kit).pack(side="right")

        self.card = theme.Card(page, title="Your installers")
        self.card.pack(fill="both", expand=True)
        self.list_frame = ctk.CTkScrollableFrame(self.card.body, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True)
        self.list_frame.grid_columnconfigure(1, weight=1)

        self.status = ctk.CTkLabel(page, text="", anchor="w", text_color=C["text_muted"])
        self.status.pack(fill="x", pady=(8, 0))

        enable_path_drop(window, page, self.add_paths, self.list_frame)
        self.refresh()
        return window

    # --- library -----------------------------------------------------------
    def current_os(self):
        return "mac" if self.os_var.get() == "Mac" else "windows"

    def os_folder(self, os_name=None):
        return Path(self.library) / (os_name or self.current_os())

    def entries(self):
        folder = self.os_folder()
        if not folder.is_dir():
            return []
        needle = self.search_var.get().strip().lower()
        items = [p for p in folder.iterdir() if p.name not in JUNK and not p.name.startswith("._")
                 and needle in p.name.lower()]
        return sorted(items, key=lambda p: p.name.lower())

    def refresh(self):
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.checks = {}
        items = self.entries()
        if not items:
            theme.muted(self.list_frame, "Nothing here yet. Drag installers onto this window or click "
                        "Add Installers.", wraplength=520).grid(row=0, column=0, columnspan=4, pady=30)
        runnable = self.current_os() == HERE_OS
        for i, path in enumerate(items):
            if i:
                theme.divider(self.list_frame).grid(row=i * 2 - 1, column=0, columnspan=4, sticky="ew", pady=2)
            var = ctk.BooleanVar(value=False)
            self.checks[path] = var
            ctk.CTkCheckBox(self.list_frame, text="", variable=var, width=24).grid(row=i * 2, column=0, padx=(0, 6))
            text = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            text.grid(row=i * 2, column=1, sticky="w", pady=6)
            ctk.CTkLabel(text, text=path.name, font=theme.font("heading"), anchor="w", height=20).pack(anchor="w")
            try:
                size = size_text(sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                                 if path.is_dir() else path.stat().st_size)
            except OSError:
                size = ""
            theme.muted(text, size, height=16).pack(anchor="w")
            if runnable:
                theme.Button(self.list_frame, "Run", kind="secondary", width=70,
                             command=lambda p=path: self.run_installer(p)).grid(row=i * 2, column=2, padx=4)
            theme.Button(self.list_frame, "Remove", kind="ghost", width=76,
                         command=lambda p=path: self.remove(p)).grid(row=i * 2, column=3)
        self.status.configure(text=f"{len(items)} installer{'s' if len(items) != 1 else ''}   |   Library: {self.library}")

    def add_dialog(self):
        types = " ".join(f"*{e}" for e in EXTENSIONS[self.current_os()])
        paths = filedialog.askopenfilenames(title="Add installers", parent=self.window,
                                            filetypes=[("Installers", types), ("All files", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def add_paths(self, paths):
        # Each file goes to the folder for its own OS, whichever tab is showing
        added, skipped = 0, []
        for raw in paths:
            src = Path(raw)
            ext = src.suffix.lower()
            target_os = "mac" if ext in (".dmg", ".pkg") else "windows" if ext in (".exe", ".msi") else self.current_os()
            if src.is_dir() or ext not in EXTENSIONS[target_os]:
                skipped.append(src.name)
                continue
            folder = self.os_folder(target_os)
            try:
                folder.mkdir(parents=True, exist_ok=True)
                dest = unique_path(folder, src.name)
                self.status.configure(text=f"Copying {src.name}...")
                self.window.update_idletasks()
                shutil.copy2(src, dest)
                added += 1
            except OSError as exc:
                messagebox.showerror("Could not add", f"{src.name}: {exc}", parent=self.window)
        self.refresh()
        if skipped:
            self.status.configure(text=f"Added {added}. Skipped (not an installer): {', '.join(skipped)}")
        elif added:
            self.status.configure(text=f"Added {added} installer{'s' if added != 1 else ''}")

    def remove(self, path):
        if not messagebox.askyesno("Remove installer", f"Delete {path.name} from the library?", parent=self.window):
            return
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        except OSError as exc:
            messagebox.showerror("Could not remove", str(exc), parent=self.window)
        self.refresh()

    def run_installer(self, path):
        if not messagebox.askyesno("Run installer", f"Open {path.name}?", parent=self.window):
            return
        self.open_path(path)

    def open_path(self, path):
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["open", str(path)])
        except OSError as exc:
            messagebox.showerror("Could not open", str(exc), parent=self.window)

    def reveal_library(self):
        folder = self.os_folder()
        folder.mkdir(parents=True, exist_ok=True)
        self.open_path(folder)

    def change_library(self):
        folder = filedialog.askdirectory(title="Choose the installers library folder", parent=self.window,
                                         initialdir=self.library if os.path.isdir(self.library) else None)
        if folder:
            self.library = folder
            self.save_settings()
            self.refresh()

    # --- Setup Kit ----------------------------------------------------------
    def send_to_kit(self):
        kit = self.kit_folder()
        if not kit or not os.path.isdir(kit):
            messagebox.showinfo("Setup Kit", "Choose a kit folder in the Setup Kit tool first.", parent=self.window)
            return
        chosen = [p for p, var in self.checks.items() if var.get()]
        if not chosen:
            messagebox.showinfo("Setup Kit", "Tick the installers to copy into the kit.", parent=self.window)
            return
        dest = Path(kit) / "installers" / self.current_os()
        if not messagebox.askyesno(
                "Send to Setup Kit",
                f"Copy {len(chosen)} installer{'s' if len(chosen) != 1 else ''} into\n{dest}?\n\n"
                "Deploy in Setup Kit runs them in numbered order.", parent=self.window):
            return
        self.status.configure(text="Copying to the kit...")
        threading.Thread(target=self._copy_to_kit, args=(chosen, dest), daemon=True).start()

    def _copy_to_kit(self, chosen, dest):
        try:
            dest.mkdir(parents=True, exist_ok=True)
            number = next_kit_number(dest)
            for path in chosen:
                name = path.name if re.match(r"\d+_", path.name) else f"{number:02d}_{path.name}"
                number += 1
                shutil.copy2(path, unique_path(dest, name))
            message = f"Copied {len(chosen)} to {dest}"
        except OSError as exc:
            message = f"Could not copy to the kit: {exc}"
        self.window.after(0, lambda: self.status.configure(text=message))
