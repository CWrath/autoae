"""
Project Backup Tool
A comprehensive backup solution for project files with cloud storage integration
Compatible with AUTO AE tool loading system
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import os
import json
import shutil
import threading
import schedule
import time
import webbrowser
import re
from datetime import datetime
import logging
import theme
from theme import C
from drag_drop import enable_path_drop
import background_services
import cloud_storage

# Try to import base_tool, fallback if not available
try:
    from base_tool import BaseTool
except ImportError:
    # Fallback base class if base_tool.py is not available
    class BaseTool:
        def __init__(self):
            self.name = "Base Tool"
            self.icon = None
            self.description = "Tool description"
        
        def create_tool_window(self, parent, title, width=800, height=600):
            window = ctk.CTkToplevel(parent)
            window.title(title)
            window.geometry(f"{width}x{height}")
            
            # Center the window
            window.update_idletasks()
            x = (window.winfo_screenwidth() // 2) - (width // 2)
            y = (window.winfo_screenheight() // 2) - (height // 2)
            window.geometry(f"{width}x{height}+{x}+{y}")
            
            return window

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

LOCAL_MODES = {
    "Dated snapshots (keep history)": "snapshots",
    "Mirror (copy new and changed files only)": "mirror",
}
SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")


def format_size(size):
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "{:.0f} {}".format(size, unit) if unit == "B" else "{:.1f} {}".format(size, unit)
        size /= 1024


def parse_patterns(text):
    """'node_modules, .git' -> ['node_modules', '.git'] (blank entries would exclude everything)."""
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def iter_files(source, patterns):
    """Relative paths of the files under source, skipping names that contain an exclude pattern."""
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not any(p in d for p in patterns)]
        for name in files:
            if not any(p in name for p in patterns):
                yield os.path.relpath(os.path.join(root, name), source)


def unique_names(paths):
    """[(path, folder name)] so two sources called 'Project' don't overwrite each other."""
    used, result = set(), []
    for path in paths:
        base = os.path.basename(os.path.normpath(path)) or "Source"
        name, number = base, 2
        while name.lower() in used:
            name = "{}_{}".format(base, number)
            number += 1
        used.add(name.lower())
        result.append((path, name))
    return result


def is_inside(child, parent):
    child = os.path.normcase(os.path.abspath(child))
    parent = os.path.normcase(os.path.abspath(parent)).rstrip(os.sep)
    return child == parent or child.startswith(parent + os.sep)


def is_current(source_file, dest_file):
    """True when dest_file already matches source_file (same size, not older). 2s slack for NAS timestamps."""
    try:
        dest = os.stat(dest_file)
    except FileNotFoundError:
        return False
    src = os.stat(source_file)
    return src.st_size == dest.st_size and src.st_mtime <= dest.st_mtime + 2


def check_destination(path):
    """(ok, message). The folder itself must exist so an unplugged drive isn't silently recreated."""
    path = (path or "").strip()
    if not path:
        return False, "Choose a destination folder first."
    try:
        if not os.path.isdir(path):
            return False, "Can't reach {}. Check that the NAS is on and the share is connected.".format(path)
        probe = os.path.join(path, ".autoae_write_test_{}".format(os.getpid()))
        with open(probe, "w") as handle:
            handle.write("ok")
        os.remove(probe)
        free = shutil.disk_usage(path).free
    except PermissionError:
        return False, "No permission to write to {}.".format(path)
    except OSError as error:
        return False, "Can't write to {}: {}".format(path, error)
    return True, "Ready: {} free".format(format_size(free))


def prune_snapshots(root, keep, log=None):
    """Delete the oldest dated snapshot folders beyond `keep` (0 = keep all). Only touches folders named like a snapshot."""
    log = log or (lambda message: None)
    if keep <= 0 or not os.path.isdir(root):
        return 0
    names = sorted(n for n in os.listdir(root) if SNAPSHOT_RE.match(n) and os.path.isdir(os.path.join(root, n)))
    removed = 0
    for name in names[:-keep]:
        try:
            shutil.rmtree(os.path.join(root, name))
            removed += 1
            log("Removed old snapshot {}".format(name))
        except OSError as error:
            log("Warning: couldn't remove old snapshot {}: {}".format(name, error))
    return removed


def run_local_backup(paths, base, folder_name, mode, patterns, keep=0, log=None, progress=None, now=None):
    """Copy paths into base/folder_name. Returns a dict of counts; raises OSError if any file failed.

    snapshots: base/folder/<timestamp>/<source name>/...  (old snapshots pruned to `keep` when all files copied)
    mirror:    base/folder/Latest/<source name>/...       (only new or changed files are copied; nothing is deleted)
    """
    log = log or (lambda message: None)
    progress = progress or (lambda fraction: None)
    ok, message = check_destination(base)
    if not ok:
        raise OSError(message)
    root = os.path.join(base, (folder_name or "").strip() or "ProjectBackups")
    sources = unique_names(paths)
    for path, _ in sources:
        if is_inside(root, path):
            raise ValueError("The destination {} is inside the project folder {}. Choose a destination outside it.".format(root, path))

    listing = [(path, name, list(iter_files(path, patterns))) for path, name in sources]
    total_files = sum(len(files) for _, _, files in listing)
    total_bytes = 0
    for path, _, files in listing:
        for rel in files:
            try:
                total_bytes += os.path.getsize(os.path.join(path, rel))
            except OSError:
                pass
    if mode == "snapshots":
        free = shutil.disk_usage(base).free
        if total_bytes > free:
            raise OSError("Not enough space on the destination: {} needed, {} free.".format(
                format_size(total_bytes), format_size(free)))
        target = os.path.join(root, (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S"))
    else:
        target = os.path.join(root, "Latest")
    os.makedirs(target, exist_ok=True)
    log("Found {} files ({}) to back up".format(total_files, format_size(total_bytes)))

    copied = skipped = failed = done_bytes = 0
    for path, name, files in listing:
        for rel in files:
            source_file = os.path.join(path, rel)
            dest_file = os.path.join(target, name, rel)
            try:
                size = os.path.getsize(source_file)
                if mode == "mirror" and is_current(source_file, dest_file):
                    skipped += 1
                else:
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    shutil.copy2(source_file, dest_file)
                    copied += 1
                done_bytes += size
            except OSError as error:
                failed += 1
                if failed <= 20:
                    log("Warning: couldn't copy {}: {}".format(source_file, error))
            if (copied + skipped + failed) % 25 == 0:
                progress(done_bytes / max(total_bytes, 1))
    progress(1.0)
    log("Processed {} files into {} ({} copied, {} unchanged, {} failed)".format(
        copied + skipped, target, copied, skipped, failed))
    if mode == "snapshots" and not failed:
        prune_snapshots(root, keep, log)
    if failed:
        raise OSError("{} files couldn't be copied; see the log.".format(failed))
    return {"copied": copied, "skipped": skipped, "failed": failed, "target": target}


CONFIG_FILE = background_services.data_dir() / "backup_config.json"
LEGACY_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "backup_config.json")


def account_from_config(config):
    provider = config.get("cloud_provider", "dropbox")
    if provider == "dropbox":
        return cloud_storage.Account(provider, config.get("dropbox_key", ""))
    return cloud_storage.Account(provider, config.get("google_client_id", ""), config.get("google_client_secret", ""))


def config_problem(config):
    """Why this saved configuration can't run, or '' when it can."""
    paths = [p for p in config.get("project_path", []) if p]
    if not paths:
        return "Please select at least one project folder"
    for path in paths:
        if not os.path.exists(path):
            return "Selected project folder does not exist: {}".format(path)
    cloud, local = config.get("cloud_enabled", True), config.get("local_enabled", False)
    if not cloud and not local:
        return "Turn on at least one destination (cloud or Local / NAS)."
    if cloud:
        account = account_from_config(config)
        problem = account.credentials_missing()
        if not problem and not account.is_connected():
            problem = "Sign in to {} first, or turn off cloud upload.".format(account.spec["label"])
        if problem:
            return problem
    if local and not str(config.get("local_path", "")).strip():
        return "Choose a Local / NAS destination folder, or turn it off."
    try:
        int(str(config.get("local_keep", "0")).strip() or "0")
    except ValueError:
        return "Snapshots to keep must be a whole number."
    return ""


def add_backup_job(scheduler, config, job):
    """Schedules job on a `schedule` scheduler. Raises ValueError for an unusable time or frequency."""
    try:
        hour, minute = int(config.get("hour", 2)), int(config.get("minute", 0))
    except (TypeError, ValueError):
        raise ValueError("Please enter valid hour and minute values") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("Please enter valid hour and minute values")
    frequency = config.get("frequency", "daily")
    if frequency == "hourly":
        return scheduler.every().hour.at(":{:02d}".format(minute)).do(job)
    if frequency == "daily":
        return scheduler.every().day.at("{:02d}:{:02d}".format(hour, minute)).do(job)
    if frequency == "weekly":
        return scheduler.every().monday.at("{:02d}:{:02d}".format(hour, minute)).do(job)
    raise ValueError("Unknown backup frequency: {}".format(frequency))


def upload_to_cloud(config, paths, patterns, log, progress):
    account = account_from_config(config)
    problem = account.credentials_missing()
    if problem:
        raise cloud_storage.AuthError(problem)
    if not account.is_connected():
        raise cloud_storage.AuthError("Not connected to {}. Use Sign in with browser first.".format(account.spec["label"]))
    listing = [(path, name, list(iter_files(path, patterns))) for path, name in unique_names(paths)]
    root_parts = cloud_storage.split_remote_path(config.get("cloud_folder", "")) + \
        cloud_storage.split_remote_path(config.get("backup_folder", ""))
    log("Uploading {} files to {}".format(sum(len(f) for _, _, f in listing), account.spec["label"]))
    cloud_storage.upload_backup(account.client(), listing, root_parts, log, progress)


def run_backup(config, log, progress=None, status=None):
    """Runs every destination the config turns on. Returns the destinations that failed ([] = all good)."""
    progress = progress or (lambda fraction: None)
    status = status or (lambda text: None)
    paths = [p for p in config.get("project_path", []) if p]
    patterns = parse_patterns(config.get("exclude_patterns", ""))
    stages = []
    if config.get("local_enabled", False):
        stages.append(("local", "Copying to Local / NAS..."))
    if config.get("cloud_enabled", True):
        stages.append(("cloud", "Uploading to cloud..."))
    if not stages:
        log("Error: no destination is turned on")
        return ["none"]
    failures = []
    for index, (kind, label) in enumerate(stages):
        status(label)

        def stage_progress(fraction, index=index):
            progress((index + min(fraction, 1.0)) / len(stages))

        try:
            if kind == "local":
                try:
                    keep = int(str(config.get("local_keep", "0")).strip() or "0")
                except ValueError:
                    keep = 0
                run_local_backup(paths, str(config.get("local_path", "")).strip(), config.get("backup_folder", ""),
                                 config.get("local_mode", "snapshots"), patterns, keep, log, stage_progress)
            else:
                upload_to_cloud(config, paths, patterns, log, stage_progress)
        except Exception as error:  # one destination failing must not stop the other
            failures.append(kind)
            log("Error: {} backup failed: {}".format("Local / NAS" if kind == "local" else "Cloud", error))
    return failures


# Replace ProjectBackupTool with Tool for AUTO AE compatibility
class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Project Backup"
        self.description = "Automated cloud backup solution for your project files"
        self.category = "Storage & Backup"
        self.icon = "cloud-upload"
        
        # Configuration file path
        self.config_file = str(CONFIG_FILE)
        self.config = self.load_config()
        
        # Backup service state
        self.backup_service_running = False
        self.backup_thread = None
        self._backup_lock = threading.Lock()
        
        # Cloud storage clients (commented out to avoid import errors)
        self.dropbox_client = None
        self.gdrive_service = None
        
        # GUI components (will be initialized in create_window)
        self.window = None
        self.main_frame = None
        self.tabview = None
        
        # Variables (will be initialized in create_window)
        self.project_path_vars = []  # List of StringVars for multiple folders
        self.file_types_var = None
        self.exclude_patterns_var = None
        self.cloud_provider_var = None
        self.dropbox_key_var = None
        self.google_id_var = None
        self.google_secret_var = None
        self.cloud_folder_var = None
        self.backup_folder_var = None
        self.frequency_var = None
        self.hour_var = None
        self.minute_var = None
        self.background_var = None
        self.cloud_enabled_var = None
        self.local_enabled_var = None
        self.local_path_var = None
        self.local_mode_var = None
        self.local_keep_var = None
        
    def create_window(self, parent):
        """Create and return a new window for this tool"""
        # Create the tool window using the base class helper
        self.window = self.create_tool_window(
            parent, 
            self.name,
            width=1000, 
            height=760
        )
        
        self.window.minsize(820, 600)
        
        # Initialize variables
        self.init_variables()
        
        # Create GUI
        self.create_widgets()
        self.load_saved_config()
        self.show_background_state()
        
        # Set up window closing protocol
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        return self.window
        
    def init_variables(self):
        """Initialize all tkinter variables"""
        self.project_path_vars = []  # List of StringVars for multiple folders
        self.file_types_var = tk.StringVar(value="*.*")
        self.exclude_patterns_var = tk.StringVar(value="node_modules, .git, __pycache__, dist, build")
        self.cloud_provider_var = tk.StringVar(value="dropbox")
        self.dropbox_key_var = tk.StringVar()
        self.google_id_var = tk.StringVar()
        self.google_secret_var = tk.StringVar()
        self.cloud_folder_var = tk.StringVar(value="Backups")
        self._connect_cancel = False
        self.backup_folder_var = tk.StringVar(value="ProjectBackups")
        self.frequency_var = tk.StringVar(value="daily")
        self.hour_var = tk.StringVar(value="02")
        self.minute_var = tk.StringVar(value="00")
        self.background_var = tk.BooleanVar(value=True)
        self.cloud_enabled_var = tk.BooleanVar(value=True)
        self.local_enabled_var = tk.BooleanVar(value=False)
        self.local_path_var = tk.StringVar()
        self.local_mode_var = tk.StringVar(value=next(iter(LOCAL_MODES)))
        self.local_keep_var = tk.StringVar(value="10")
        self.folder_rows = {}
        self._folder_row_counter = 0
        
    def create_widgets(self):
        # Main container with padding
        self.main_frame = theme.page(self.window)

        # Header (icon tile + name + description)
        theme.tool_header(self.main_frame, self).pack(fill="x", pady=(0, 12))

        # Create notebook for tabs
        self.tabview = ctk.CTkTabview(self.main_frame, anchor="nw")
        self.tabview.pack(fill="both", expand=True)
        
        # Add tabs
        self.tabview.add("Source")
        self.tabview.add("Cloud Storage")
        self.tabview.add("Local / NAS")
        self.tabview.add("Schedule")
        self.tabview.add("Status")
        
        self.create_source_tab()
        self.create_cloud_tab()
        self.create_local_tab()
        self.create_schedule_tab()
        self.create_status_tab()
        
    def create_source_tab(self):
        tab = self.tabview.tab("Source")
        
        # Source folder selection (multiple)
        source_card = theme.Card(tab, title="Project folders")
        source_card.pack(fill="x", pady=(4, 0))
        source_frame = source_card.body
        
        self.folder_list_frame = ctk.CTkFrame(source_frame, fg_color="transparent")
        self.folder_list_frame.pack(fill="x")
        self.folder_list_frame.grid_columnconfigure(0, weight=1)
        
        self.add_folder_input()  # Add initial folder input
        
        add_folder_btn = theme.Button(source_frame, "Add Folder", kind="secondary", width=120,
                                      image=theme.icon("plus", C["text"], 15), compound="left",
                                      command=self.add_folder_input)
        add_folder_btn.pack(anchor="w", pady=(8, 0))
        
        # File types frame
        types_card = theme.Card(tab, title="Filters")
        types_card.pack(fill="x", pady=(12, 0))
        types_frame = types_card.body
        theme.form_row(types_frame, 0, "Include", ctk.CTkEntry(
            types_frame, textvariable=self.file_types_var, placeholder_text="*.js, *.py, *.html, *.css", height=34))
        theme.form_row(types_frame, 1, "Exclude", ctk.CTkEntry(
            types_frame, textvariable=self.exclude_patterns_var, placeholder_text="Folders/files to exclude", height=34),
            pady=(0, 0))
        
        # Preview frame
        preview_card = theme.Card(tab, title="Folder preview")
        preview_card.pack(fill="both", expand=True, pady=(12, 0))
        preview_frame = preview_card.body
        
        theme.Button(preview_frame, "Refresh Preview", kind="secondary", width=140,
                     image=theme.icon("refresh-cw", C["text"], 15), compound="left",
                     command=self.refresh_preview).pack(side="bottom", anchor="w", pady=(10, 0))
        self.preview_text = ctk.CTkTextbox(preview_frame, height=120, font=theme.font("mono"))
        self.preview_text.pack(fill="both", expand=True)
        
    def add_folder_input(self):
        var = tk.StringVar()
        self.project_path_vars.append(var)
        row = self._folder_row_counter
        self._folder_row_counter += 1
        
        frame = ctk.CTkFrame(self.folder_list_frame, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self.folder_rows[id(var)] = frame
        
        entry = ctk.CTkEntry(frame, textvariable=var, width=400, height=34)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        
        browse_btn = theme.Button(frame, "Browse…", kind="secondary", width=92, command=lambda v=var: self.browse_folder_var(v))
        browse_btn.pack(side="left", padx=(0, 8))
        
        remove_btn = theme.Button(frame, "Remove", kind="danger", width=84, command=lambda: self.remove_folder_input(var, frame))
        remove_btn.pack(side="left")
        return var
        
    def remove_folder_input(self, var, frame):
        if var in self.project_path_vars:
            self.project_path_vars.remove(var)
        self.folder_rows.pop(id(var), None)
        frame.destroy()
        
    def browse_folder_var(self, var):
        folder = filedialog.askdirectory(title="Select Project Folder")
        if folder:
            var.set(folder)
            self.refresh_preview()
            
    def create_cloud_tab(self):
        tab = self.tabview.tab("Cloud Storage")
        
        # Cloud provider selection
        provider_card = theme.Card(tab, title="Cloud provider")
        provider_card.pack(fill="x", pady=(4, 0))
        provider_buttons_frame = provider_card.body
        
        self.dropbox_radio = ctk.CTkRadioButton(
            provider_buttons_frame, 
            text="Dropbox", 
            variable=self.cloud_provider_var, 
            value="dropbox",
            command=self.on_provider_change,
            radiobutton_width=18,
            radiobutton_height=18,
        )
        self.dropbox_radio.pack(side="left", padx=(0, 20))
        
        self.gdrive_radio = ctk.CTkRadioButton(
            provider_buttons_frame, 
            text="Google Drive", 
            variable=self.cloud_provider_var, 
            value="google_drive",
            command=self.on_provider_change,
            radiobutton_width=18,
            radiobutton_height=18,
        )
        self.gdrive_radio.pack(side="left")
        
        # Authentication frame
        auth_card = theme.Card(tab, title="Authentication")
        auth_card.pack(fill="x", pady=(12, 0))
        auth_frame = auth_card.body
        
        auth_frame.grid_columnconfigure(0, weight=1)
        self.cred_frame = ctk.CTkFrame(auth_frame, fg_color="transparent")
        self.cred_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.cred_id_entry = ctk.CTkEntry(self.cred_frame, height=34)
        theme.form_row(self.cred_frame, 0, "App key / client ID", self.cred_id_entry, label_width=150)
        self.cred_secret_entry = ctk.CTkEntry(self.cred_frame, height=34, show="*")
        theme.form_row(self.cred_frame, 1, "Client secret", self.cred_secret_entry, label_width=150, pady=(0, 0))

        action_row = ctk.CTkFrame(auth_frame, fg_color="transparent")
        action_row.grid(row=0, column=0, sticky="ew")
        self.advanced_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(auth_frame, text="Advanced: use my own app keys", variable=self.advanced_var,
                        command=self.refresh_cloud_state, checkbox_width=18,
                        checkbox_height=18).grid(row=2, column=0, sticky="w", pady=(10, 0))
        # Colour follows the text: green once connected
        self.auth_status_label = theme.StatusLabel(
            action_row, text="Not connected",
            rules=[("Not", "text_muted"), ("Connected", "success"), ("Opening", "text_muted"),
                   ("Waiting", "text_muted")])
        self.auth_status_label.pack(side="left")
        theme.Button(action_row, "Setup help", kind="ghost", width=100,
                     command=self.show_cloud_help).pack(side="right")
        self.disconnect_button = theme.Button(action_row, "Disconnect", kind="ghost", width=100,
                                              command=self.disconnect_cloud_storage)
        self.disconnect_button.pack(side="right", padx=(0, 8))
        self.connect_button = theme.Button(
            action_row, "Sign in with browser?", kind="secondary", width=190,
            command=self.connect_cloud_storage)
        self.connect_button.pack(side="right", padx=(0, 8))
        self.refresh_cloud_state()

        # Backup settings frame
        settings_card = theme.Card(tab, title="Backup settings")
        settings_card.pack(fill="x", pady=(12, 0))
        settings_frame = settings_card.body
        theme.form_row(settings_frame, 0, "Cloud folder",
                       ctk.CTkEntry(settings_frame, textvariable=self.cloud_folder_var, height=34,
                                    placeholder_text="Backups   or   Work/Projects"),
                       label_width=150)
        theme.form_row(settings_frame, 1, "Backup folder name",
                       ctk.CTkEntry(settings_frame, textvariable=self.backup_folder_var, height=34),
                       label_width=150, pady=(0, 0))
        theme.muted(settings_frame, "Files are uploaded to <Cloud folder>/<Backup folder name>/<project name>/. "
                    "Missing folders are created. Existing files are overwritten.", role="body",
                    wraplength=640, justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ctk.CTkCheckBox(
            settings_frame,
            text="Upload to cloud storage when backing up",
            variable=self.cloud_enabled_var,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=3, column=1, sticky="w", pady=(10, 0))
        
    def create_local_tab(self):
        tab = self.tabview.tab("Local / NAS")
        card = theme.Card(tab, title="Local or NAS backup")
        card.pack(fill="x", pady=(4, 0))
        body = card.body
        body.grid_columnconfigure(1, weight=1)

        ctk.CTkCheckBox(
            body,
            text="Save a copy to a local drive or network share (NAS)",
            variable=self.local_enabled_var,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        dest_row = ctk.CTkFrame(body, fg_color="transparent")
        self.local_entry = ctk.CTkEntry(dest_row, textvariable=self.local_path_var, height=34,
                                        placeholder_text=r"\\NAS\Backups   or   Z:\Backups")
        self.local_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        theme.Button(dest_row, "Browse?", kind="secondary", width=92,
                     command=self.browse_local_destination).pack(side="left", padx=(0, 8))
        theme.Button(dest_row, "Test connection", kind="ghost", width=130,
                     command=self.test_local_destination).pack(side="left")
        theme.form_row(body, 1, "Destination", dest_row)

        self.local_status_label = theme.StatusLabel(
            body, text="", rules=[("Ready", "success"), ("Can't", "danger"), ("No permission", "danger"),
                                  ("Choose", "text_muted"), ("Checking", "text_muted")])
        self.local_status_label.grid(row=2, column=1, sticky="w", pady=(0, 10))

        theme.form_row(body, 3, "Method", ctk.CTkOptionMenu(
            body, values=list(LOCAL_MODES), variable=self.local_mode_var, width=320, height=34), sticky="w")

        keep_row = ctk.CTkFrame(body, fg_color="transparent")
        ctk.CTkEntry(keep_row, textvariable=self.local_keep_var, width=60, height=34, justify="center").pack(side="left")
        theme.muted(keep_row, "snapshots to keep; older ones are deleted after a good backup (0 = keep all)").pack(
            side="left", padx=(10, 0))
        theme.form_row(body, 4, "Keep", keep_row, sticky="w", pady=(0, 10))

        theme.muted(body, "Files go into <destination>/<Backup folder name>/. Snapshots make a new dated folder each run; "
                          "Mirror keeps one 'Latest' folder and only copies what changed. "
                          "Backups never delete files from your project folders.", role="body",
                    wraplength=640, justify="left").grid(row=5, column=0, columnspan=2, sticky="w")

        enable_path_drop(self.window, self.local_entry, self.drop_local_destination)

    def browse_local_destination(self):
        folder = filedialog.askdirectory(title="Select backup destination (local drive or NAS share)")
        if folder:
            self.local_path_var.set(os.path.normpath(folder))
            self.test_local_destination()

    def drop_local_destination(self, paths):
        path = next((p for p in paths if os.path.exists(p)), None)
        if path:
            self.local_path_var.set(os.path.normpath(path if os.path.isdir(path) else os.path.dirname(path)))
            self.test_local_destination()

    def test_local_destination(self):
        path = self.local_path_var.get().strip()
        self.local_status_label.configure(text="Checking?")

        def work():
            ok, message = check_destination(path)
            if self.window and self.window.winfo_exists():
                self.window.after(0, lambda: self.local_status_label.configure(text=message))
        threading.Thread(target=work, daemon=True).start()

    def local_mode(self):
        return LOCAL_MODES.get(self.local_mode_var.get(), "snapshots")

    def create_schedule_tab(self):
        tab = self.tabview.tab("Schedule")
        
        # Schedule settings frame
        schedule_card = theme.Card(tab, title="Backup schedule")
        schedule_card.pack(fill="x", pady=(4, 0))
        schedule_frame = schedule_card.body
        
        # Frequency selection
        frequency_menu = ctk.CTkOptionMenu(
            schedule_frame, 
            values=["hourly", "daily", "weekly"],
            variable=self.frequency_var,
            width=160,
            height=34,
        )
        theme.form_row(schedule_frame, 0, "Frequency", frequency_menu, sticky="w")
        
        # Time selection
        time_input_frame = ctk.CTkFrame(schedule_frame, fg_color="transparent")
        ctk.CTkEntry(time_input_frame, textvariable=self.hour_var, width=52, height=34, justify="center",
                     placeholder_text="HH", font=theme.font("mono")).pack(side="left")
        ctk.CTkLabel(time_input_frame, text=":", font=theme.font("body_strong")).pack(side="left", padx=6)
        ctk.CTkEntry(time_input_frame, textvariable=self.minute_var, width=52, height=34, justify="center",
                     placeholder_text="MM", font=theme.font("mono")).pack(side="left")
        theme.muted(time_input_frame, "24-hour time").pack(side="left", padx=(10, 0))
        theme.form_row(schedule_frame, 1, "Backup time", time_input_frame, sticky="w")
        
        # Background service option
        ctk.CTkCheckBox(
            schedule_frame, 
            text="Keep running from the tray icon when I close Auto AE",
            variable=self.background_var,
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=2, column=1, sticky="w", pady=(4, 0))
        
        # Control buttons
        control_card = theme.Card(tab, title="Backup service")
        control_card.pack(fill="x", pady=(12, 0))
        button_frame = control_card.body
        
        self.start_button = theme.Button(
            button_frame, 
            "Start Backup Service", 
            kind="primary",
            width=180,
            command=self.start_backup_service,
        )
        self.start_button.pack(side="left", padx=(0, 8))
        
        self.stop_button = theme.Button(
            button_frame, 
            "Stop Backup Service", 
            kind="danger",
            width=170,
            command=self.stop_backup_service,
            state="disabled"
        )
        self.stop_button.pack(side="left", padx=(0, 8))
        
        self.backup_now_button = theme.Button(
            button_frame, 
            "Backup Now", 
            kind="secondary",
            width=120,
            command=self.backup_now
        )
        self.backup_now_button.pack(side="left")
        
    def create_status_tab(self):
        tab = self.tabview.tab("Status")
        
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", pady=(4, 0))
        top.grid_columnconfigure((0, 1), weight=1, uniform="status")
        
        # Status info frame
        status_card = theme.Card(top, title="Service status")
        status_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        status_info_frame = status_card.body
        
        self.service_status_label = theme.StatusLabel(
            status_info_frame, text="Service Stopped", rules=[("Running", "success")], height=22)
        self.service_status_label.pack(anchor="w")
        
        self.next_backup_label = theme.muted(status_info_frame, "Next backup: Not scheduled", role="body", height=20)
        self.next_backup_label.pack(anchor="w")
        
        self.last_backup_label = theme.muted(status_info_frame, "Last backup: Never", role="body", height=20)
        self.last_backup_label.pack(anchor="w")
        
        # Progress frame
        progress_card = theme.Card(top, title="Backup progress")
        progress_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        progress_frame = progress_card.body
        
        self.progress_label = ctk.CTkLabel(progress_frame, text="Ready", anchor="w", height=22)
        self.progress_label.pack(fill="x")
        
        self.progress_bar = theme.ProgressBar(progress_frame)
        self.progress_bar.pack(fill="x", pady=(8, 0))
        self.progress_bar.set(0)
        
        # Log frame
        log_card = theme.Card(tab, title="Backup log")
        log_card.pack(fill="both", expand=True, pady=(12, 0))
        
        # Save/Load configuration
        config_button_frame = ctk.CTkFrame(log_card.body, fg_color="transparent")
        config_button_frame.pack(side="bottom", fill="x", pady=(10, 0))
        theme.Button(config_button_frame, "Save Config", kind="secondary", width=110,
                     command=self.save_config).pack(side="left", padx=(0, 8))
        theme.Button(config_button_frame, "Load Config", kind="ghost", width=110,
                     command=self.load_saved_config).pack(side="left")
        
        self.log_text = theme.LogBox(log_card.body, empty_text="Backup activity will be listed here.",
                                     wrap="word", height=140)
        self.log_text.pack(fill="both", expand=True)
        
    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Project Folder")
        if folder:
            self.project_path_var.set(folder)
            self.refresh_preview()
            
    def refresh_preview(self):
        paths = [v.get() for v in self.project_path_vars if v.get()]
        if not paths:
            self.preview_text.delete("0.0", "end")
            self.preview_text.insert("0.0", "No valid folder selected")
            return
            
        try:
            exclude_patterns = parse_patterns(self.exclude_patterns_var.get())
            file_list = []
            
            for path in paths:
                if not os.path.exists(path):
                    continue
                for root, dirs, files in os.walk(path):
                    dirs[:] = [d for d in dirs if not any(pattern in d for pattern in exclude_patterns)]
                    
                    for file in files:
                        if not any(pattern in file for pattern in exclude_patterns):
                            rel_path = os.path.relpath(os.path.join(root, file), path)
                            file_list.append(f"{os.path.basename(path)}/" + rel_path)
                            
                if len(file_list) > 100:  # Limit preview
                    file_list.append("... (truncated)")
                    break
                    
            self.preview_text.delete("0.0", "end")
            self.preview_text.insert("0.0", "\n".join(file_list))
            
        except Exception as e:
            self.preview_text.delete("0.0", "end")
            self.preview_text.insert("0.0", f"Error reading folder: {str(e)}")
            
    def current_account(self):
        provider = self.cloud_provider_var.get()
        if provider == "dropbox":
            return cloud_storage.Account(provider, self.dropbox_key_var.get())
        return cloud_storage.Account(provider, self.google_id_var.get(), self.google_secret_var.get())

    def refresh_cloud_state(self):
        google = self.cloud_provider_var.get() == "google_drive"
        self.cred_id_entry.configure(textvariable=self.google_id_var if google else self.dropbox_key_var)
        self.cred_secret_entry.configure(textvariable=self.google_secret_var,
                                         state="normal" if google else "disabled")
        has_default = bool(cloud_storage.DEFAULT_CLIENTS[self.cloud_provider_var.get()]["client_id"])
        custom = bool(self.google_id_var.get().strip() if google else self.dropbox_key_var.get().strip())
        if has_default and not custom and not self.advanced_var.get():
            self.cred_frame.grid_remove()
        else:
            self.cred_frame.grid()
        account = self.current_account()
        if account.is_connected() and not account.credentials_missing():
            who = account.connected_as()
            self.auth_status_label.configure(text="Connected" + (" as " + who if who else ""))
        else:
            self.auth_status_label.configure(text="Not connected")

    def on_provider_change(self):
        self.refresh_cloud_state()
        self.log_message(f"Switched to {cloud_storage.PROVIDERS[self.cloud_provider_var.get()]['label']}")

    def show_cloud_help(self):
        if self.cloud_provider_var.get() == "dropbox":
            text = (
                "One-time setup (about 2 minutes):\n\n"
                "1. Open dropbox.com/developers/apps and click Create app.\n"
                "2. Choose Scoped access, then App folder (safest) or Full Dropbox.\n"
                "3. Permissions tab: tick files.content.write and files.metadata.read, then Submit.\n"
                "4. Settings tab: add this Redirect URI and click Add:\n"
                f"   {cloud_storage.REDIRECT_URI}\n"
                "5. Copy the App key here, then click Sign in with browser.\n\n"
                "With an App folder app, everything goes inside Apps/<your app name>/.")
            url = "https://www.dropbox.com/developers/apps"
        else:
            text = (
                "One-time setup (about 5 minutes):\n\n"
                "1. Open console.cloud.google.com and create a project.\n"
                "2. APIs & Services > Library: enable Google Drive API.\n"
                "3. OAuth consent screen: choose External, add yourself as a Test user.\n"
                "4. Credentials > Create credentials > OAuth client ID > Desktop app.\n"
                "5. Copy the Client ID and Client secret here, then click Sign in with browser.\n\n"
                "Access is limited to files and folders this app creates (drive.file). "
                "While the consent screen is in Testing, Google asks you to sign in again every 7 days.")
            url = "https://console.cloud.google.com/apis/credentials"
        if messagebox.askyesno("Setup help", text + "\n\nOpen the developer page now?"):
            webbrowser.open(url)

    def connect_cloud_storage(self):
        account = self.current_account()
        problem = account.credentials_missing()
        if problem:
            self.advanced_var.set(True)
            self.refresh_cloud_state()
            messagebox.showerror("Missing details", problem + "\nClick Setup help for the steps.")
            return
        label = account.spec["label"]
        self.connect_button.configure(state="disabled")
        self.auth_status_label.configure(text="Waiting for browser sign-in?")
        self.log_message(f"Opening {label} sign-in in your browser")

        def work():
            try:
                who = account.connect()
                result = ("ok", who)
            except cloud_storage.CloudError as error:
                result = ("error", str(error))
            if self.window and self.window.winfo_exists():
                self.window.after(0, lambda: self.finish_connect(label, result))
        threading.Thread(target=work, daemon=True).start()

    def finish_connect(self, label, result):
        self.connect_button.configure(state="normal")
        if result[0] == "ok":
            self.log_message(f"Connected to {label}" + (f" as {result[1]}" if result[1] else ""))
        else:
            self.log_message(f"{label} sign-in failed: {result[1]}")
            messagebox.showerror("Sign-in failed", result[1])
        self.refresh_cloud_state()

    def disconnect_cloud_storage(self):
        self.current_account().disconnect()
        self.log_message("Disconnected")
        self.refresh_cloud_state()

    def start_backup_service(self):
        if not self.validate_configuration():
            return
            
        background_services.set_service("backup", False)
        schedule.clear()
        try:
            add_backup_job(schedule.default_scheduler, self.collect_config(), self.perform_backup)
        except ValueError as error:
            messagebox.showerror("Invalid schedule", str(error))
            return
        self.backup_service_running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
            
        # Start scheduler thread
        self.backup_thread = threading.Thread(target=self.run_scheduler, daemon=True)
        self.backup_thread.start()
        
        self.service_status_label.configure(text="Service Running")
        self.update_next_backup_time()
        self.log_message("Backup service started")
        
        # Save configuration
        self.save_config()
        
    def stop_backup_service(self):
        self.backup_service_running = False
        schedule.clear()
        background_services.set_service("backup", False)
        
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.service_status_label.configure(text="Service Stopped")
        self.next_backup_label.configure(text="Next backup: Not scheduled")
        
        self.log_message("Backup service stopped")
        
    def backup_now(self):
        if not self.validate_configuration():
            return
            
        # Run backup in separate thread
        backup_thread = threading.Thread(target=self.perform_backup, daemon=True)
        backup_thread.start()
        
    def validate_configuration(self):
        problem = config_problem(self.collect_config())
        if problem:
            messagebox.showerror("Configuration Error", problem)
        return not problem
        
    def run_scheduler(self):
        while self.backup_service_running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
            
            # Update next backup time
            if self.window and self.window.winfo_exists():
                self.window.after(0, self.update_next_backup_time)
            
    def perform_backup(self):
        if not self._backup_lock.acquire(blocking=False):
            self.log_message("A backup is already running; skipped this one.")
            return
        try:
            self._perform_backup()
        finally:
            self._backup_lock.release()

    def _perform_backup(self):
        self.log_message("Starting backup...")
        self.progress_bar.set(0)
        self.progress_label.configure(text="Preparing backup...")
        failures = run_backup(self.collect_config(), self.log_message, self.progress_bar.set,
                              lambda text: self.progress_label.configure(text=text))
        if failures:
            self.progress_label.configure(text="Backup finished with errors")
            self.log_message("Backup finished with errors")
            return
        self.progress_bar.set(1.0)
        self.progress_label.configure(text="Backup completed successfully")
        self.last_backup_label.configure(text=f"Last backup: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log_message("Done: backup completed successfully")

    def update_next_backup_time(self):
        if schedule.jobs:
            next_run = schedule.next_run()
            if next_run:
                self.next_backup_label.configure(
                    text=f"Next backup: {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                
    def log_message(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        
        if hasattr(self, 'log_text') and self.log_text:
            self.log_text.insert("end", log_entry)
            self.log_text.see("end")
        
        logger.info(message)
        
    def collect_config(self):
        return {
            "project_path": [v.get() for v in self.project_path_vars],
            "file_types": self.file_types_var.get(),
            "exclude_patterns": self.exclude_patterns_var.get(),
            "cloud_provider": self.cloud_provider_var.get(),
            "dropbox_key": self.dropbox_key_var.get().strip(),
            "google_client_id": self.google_id_var.get().strip(),
            "google_client_secret": self.google_secret_var.get().strip(),
            "cloud_folder": self.cloud_folder_var.get().strip(),
            "backup_folder": self.backup_folder_var.get(),
            "frequency": self.frequency_var.get(),
            "hour": self.hour_var.get(),
            "minute": self.minute_var.get(),
            "background": self.background_var.get(),
            "cloud_enabled": self.cloud_enabled_var.get(),
            "local_enabled": self.local_enabled_var.get(),
            "local_path": self.local_path_var.get().strip(),
            "local_mode": self.local_mode(),
            "local_keep": self.local_keep_var.get().strip()
        }

    def save_config(self):
        config = self.collect_config()
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            self.log_message("Configuration saved")
        except Exception as e:
            self.log_message(f"Failed to save configuration: {str(e)}")
            
    def load_config(self):
        try:
            for path in (self.config_file, LEGACY_CONFIG_FILE):
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load configuration: {str(e)}")
        return {}
        
    def load_saved_config(self):
        self.config = self.load_config()
        if self.config:
            project_paths = [p for p in self.config.get("project_path", []) if p]
            if project_paths:
                for var in list(self.project_path_vars):
                    self.remove_folder_input(var, self.folder_rows[id(var)])
                for path in project_paths:
                    self.add_folder_input().set(path)
                
            self.file_types_var.set(self.config.get("file_types", "*.*"))
            self.exclude_patterns_var.set(self.config.get("exclude_patterns", "node_modules, .git, __pycache__"))
            self.cloud_provider_var.set(self.config.get("cloud_provider", "dropbox"))
            self.dropbox_key_var.set(self.config.get("dropbox_key", ""))
            self.google_id_var.set(self.config.get("google_client_id", ""))
            self.google_secret_var.set(self.config.get("google_client_secret", ""))
            self.cloud_folder_var.set(self.config.get("cloud_folder", "Backups"))
            self.refresh_cloud_state()
            self.backup_folder_var.set(self.config.get("backup_folder", "ProjectBackups"))
            self.frequency_var.set(self.config.get("frequency", "daily"))
            self.hour_var.set(self.config.get("hour", "02"))
            self.minute_var.set(self.config.get("minute", "00"))
            self.background_var.set(self.config.get("background", True))
            self.cloud_enabled_var.set(self.config.get("cloud_enabled", True))
            self.local_enabled_var.set(self.config.get("local_enabled", False))
            self.local_path_var.set(self.config.get("local_path", ""))
            mode_code = self.config.get("local_mode", "snapshots")
            self.local_mode_var.set(next((label for label, code in LOCAL_MODES.items() if code == mode_code),
                                         next(iter(LOCAL_MODES))))
            self.local_keep_var.set(str(self.config.get("local_keep", "10")))
            
    def hand_over_or_stop(self):
        """Called when the window or the app closes. Scheduled backups carry on from the tray icon if possible."""
        if not self.backup_service_running:
            return
        self.backup_service_running = False
        schedule.clear()
        if not self.background_var.get():
            return
        self.save_config()
        if background_services.handover("backup"):
            self.log_message("Scheduled backups will keep running from the tray icon")
        else:
            self.log_message("The tray icon isn't available, so scheduled backups stop when Auto AE closes")

    def on_closing(self):
        self.hand_over_or_stop()
        if self.window:
            self.window.destroy()

    def close_app(self):
        self.hand_over_or_stop()

    def show_background_state(self):
        if background_services.agent_has("backup"):
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.service_status_label.configure(text="Service Running in the background (tray icon)")

# Function to create tool instance for the tool loader
def create_tool():
    """Factory function for the tool loader"""
    return Tool()

# For compatibility with AUTO AE tool loading system
def get_tool():
    """Alternative factory function"""
    return Tool()