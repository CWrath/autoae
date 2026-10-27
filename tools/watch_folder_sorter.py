import json
import os
import queue
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import background_services
import error_help
import theme
from base_tool import BaseTool
from theme import C
from tools.batch_renamer_tool import DATE_FORMATS, JOINERS, TokenFlow, chip_label, smart_join


def _data_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AUTO_AE"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AUTO_AE"


RULES_FILE = _data_dir() / "watch_folder_sorter_rules.json"
TOKEN_LABELS = {
    "original": "Original name",
    "folder": "Watch folder",
    "date": "Date received",
    "text": "Custom text",
}
PATH_BREAKING_CHARS = set(error_help.WINDOWS_BAD_CHARS)


def clean_original_name(name, remove_path_breaking_chars=False, uppercase=False):
    if uppercase:
        name = name.upper()
    if remove_path_breaking_chars:
        name = sanitize_path_breaking_chars(name)
    return name


def sanitize_path_breaking_chars(name):
    return "".join(
        character for character in name
        if character not in PATH_BREAKING_CHARS and ord(character) >= 32
    ).rstrip(" .")


def new_rule():
    return {
        "id": uuid.uuid4().hex,
        "name": "New Rule",
        "watch_folder": "",
        "destination_folder": "",
        "layout": [{"kind": "original"}],
        "join": "_",
        "date_format": "YYMMDD",
        "remove_path_breaking_chars": False,
        "uppercase_original": False,
        "skip_existing_tokens": True,
    }


def load_rules(path=RULES_FILE):
    path = Path(path)
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as handle:
        saved = json.load(handle)
    if not isinstance(saved, dict) or not isinstance(saved.get("rules"), list):
        raise ValueError("Auto Ingest settings must contain a rules list.")
    rules = []
    for item in saved["rules"]:
        if not isinstance(item, dict):
            raise ValueError("A saved watch rule is not a valid object.")
        rule = new_rule()
        rule.update({
            key: item[key]
            for key in (
                "id", "name", "watch_folder", "destination_folder", "layout", "join",
                "date_format", "remove_path_breaking_chars", "uppercase_original",
                "skip_existing_tokens",
            )
            if key in item
        })
        if not isinstance(rule["layout"], list):
            raise ValueError(f"The token layout for {rule['name']!r} is invalid.")
        if any(
            not isinstance(token, dict)
            or token.get("kind") not in TOKEN_LABELS
            or (token.get("kind") == "text" and not isinstance(token.get("text", ""), str))
            for token in rule["layout"]
        ):
            raise ValueError(f"The token layout for {rule['name']!r} contains an invalid token.")
        rules.append(rule)
    return rules


def load_saved_tokens(path=RULES_FILE):
    path = Path(path)
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as handle:
        saved = json.load(handle)
    if not isinstance(saved, dict):
        raise ValueError("Auto Ingest settings must contain an object.")
    tokens = saved.get("saved_tokens", [])
    if not isinstance(tokens, list) or any(not isinstance(token, str) for token in tokens):
        raise ValueError("The saved custom token bank is invalid.")
    return [token for token in tokens if token]


def save_rules(rules, path=RULES_FILE, saved_tokens=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        if saved_tokens is None and path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                previous = json.load(handle)
            saved_tokens = previous.get("saved_tokens", []) if isinstance(previous, dict) else []
        if saved_tokens is None:
            saved_tokens = []
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"rules": rules, "saved_tokens": saved_tokens}, handle, indent=2)
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def build_output_path(source_path, rule, received_at=None):
    source = Path(source_path)
    watch_folder = Path(rule["watch_folder"]).resolve()
    try:
        relative = source.resolve().relative_to(watch_folder)
    except ValueError as error:
        raise ValueError(f"File is outside the watch folder: {source}") from error

    received_at = received_at or datetime.now()
    date_format = DATE_FORMATS.get(rule.get("date_format"), DATE_FORMATS["YYMMDD"])
    date_text = received_at.strftime(date_format)
    values = {
        "original": clean_original_name(
            source.stem,
            rule.get("remove_path_breaking_chars", False),
            rule.get("uppercase_original", False),
        ),
        "folder": watch_folder.name,
        "date": date_text,
    }
    original_folded = source.stem.casefold()
    parts = []
    for token in rule.get("layout", []):
        kind = token.get("kind")
        if kind == "text":
            value = str(token.get("text", ""))
        elif kind in values:
            value = values[kind]
        else:
            raise ValueError(f"Unknown name token: {kind!r}")
        if (
            rule.get("skip_existing_tokens", True)
            and kind != "original"
            and value
            and value.casefold() in original_folded
        ):
            continue
        parts.append(value)

    stem = smart_join(parts, JOINERS.get(rule.get("join", "_"), "_"))
    if rule.get("remove_path_breaking_chars", False):
        stem = sanitize_path_breaking_chars(stem)
    if not stem.strip():
        raise ValueError("The naming pattern produced an empty file name.")
    invalid = error_help.name_problems(stem + source.suffix)
    if invalid:
        raise ValueError(f"Invalid generated file name {stem + source.suffix!r}: " + "; ".join(invalid))

    return (
        Path(rule["destination_folder"])
        / date_text
        / relative.parent
        / f"{stem}{source.suffix}"
    )


def available_destination(path):
    path = Path(path)
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def move_incoming_file(source_path, rule, received_at=None):
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Incoming media file not found: {source}")
    destination = build_output_path(source, rule, received_at)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = available_destination(destination)
    shutil.move(str(source), str(destination))
    return destination


def validate_rules(rules):
    errors = []
    names = set()
    watch_roots = []
    destination_roots = []
    for rule in rules:
        name = str(rule.get("name", "")).strip()
        if not name:
            errors.append("Every rule needs a name.")
        elif name.casefold() in names:
            errors.append(f"Rule names must be unique: {name}")
        names.add(name.casefold())

        watch_text = str(rule.get("watch_folder", "")).strip()
        destination_text = str(rule.get("destination_folder", "")).strip()
        if not watch_text or not Path(watch_text).is_dir():
            errors.append(f"{name or 'Rule'}: watch folder does not exist: {watch_text or '(not set)'}")
        if not destination_text or not Path(destination_text).is_dir():
            errors.append(
                f"{name or 'Rule'}: destination folder does not exist: {destination_text or '(not set)'}"
            )
        if watch_text and Path(watch_text).is_dir():
            watch_roots.append((name, Path(watch_text).resolve()))
        if destination_text and Path(destination_text).is_dir():
            destination_roots.append((name, Path(destination_text).resolve()))

        if rule.get("date_format") not in DATE_FORMATS:
            errors.append(f"{name or 'Rule'}: choose a valid date format.")
        if rule.get("join") not in JOINERS:
            errors.append(f"{name or 'Rule'}: choose a valid token separator.")
        layout = rule.get("layout")
        if not isinstance(layout, list) or not layout:
            errors.append(f"{name or 'Rule'}: add at least one filename token.")
        elif any(
            not isinstance(token, dict) or token.get("kind") not in TOKEN_LABELS
            or (token.get("kind") == "text" and not token.get("text", ""))
            for token in layout
        ):
            errors.append(f"{name or 'Rule'}: the filename pattern contains an invalid token.")

    for index, (name, first) in enumerate(watch_roots):
        for other_name, second in watch_roots[index + 1:]:
            if first == second or first in second.parents or second in first.parents:
                errors.append(f"Watch folders for {name!r} and {other_name!r} overlap.")

    for watch_name, watch_root in watch_roots:
        for destination_name, destination_root in destination_roots:
            if watch_root == destination_root or watch_root in destination_root.parents or destination_root in watch_root.parents:
                errors.append(
                    f"Destination for {destination_name!r} overlaps the watch folder for {watch_name!r}."
                )
    return errors


class FileHandler(FileSystemEventHandler):
    def __init__(self, sorter, rule):
        self.sorter = sorter
        self.rule = rule

    def on_created(self, event):
        if event.is_directory:
            self.sorter.queue_directory_scan(event.src_path, self.rule)
        else:
            self.sorter.queue_file(event.src_path, self.rule)

    def on_moved(self, event):
        if event.is_directory:
            self.sorter.queue_directory_scan(event.dest_path, self.rule)
        else:
            self.sorter.queue_file(event.dest_path, self.rule)


class IngestEngine:
    """Watching and moving files, with no window. Auto Ingest's tool and the tray agent both use it."""

    def _init_engine(self):
        self._active_files = set()
        self._active_files_lock = threading.Lock()
        self._move_lock = threading.Lock()

    def log(self, message):
        print(message)

    def start_observer(self, rules):
        observer = Observer()
        try:
            for rule in rules:
                observer.schedule(FileHandler(self, rule), rule["watch_folder"], recursive=True)
            observer.start()
        except (OSError, RuntimeError):
            observer.stop()
            raise
        return observer

    def queue_file(self, path, rule):
        source = Path(path)
        key = os.path.normcase(str(source.resolve()))
        with self._active_files_lock:
            if key in self._active_files:
                return
            self._active_files.add(key)
        threading.Thread(
            target=self._watch_file_worker, args=(source, rule, key), daemon=True,
        ).start()

    def queue_directory_scan(self, path, rule):
        threading.Thread(
            target=self._scan_new_directory, args=(Path(path), rule), daemon=True,
        ).start()

    def _scan_new_directory(self, directory, rule):
        time.sleep(1)
        if not directory.is_dir():
            return
        try:
            for source in directory.rglob("*"):
                if source.is_file():
                    self.queue_file(source, rule)
        except OSError as error:
            self.log(f"Error scanning new folder {directory}: {error}")

    def _watch_file_worker(self, source, rule, key):
        try:
            self.process_stable_file(source, rule, claimed=True)
        finally:
            with self._active_files_lock:
                self._active_files.discard(key)

    def process_stable_file(self, source, rule, claimed=False):
        source = Path(source)
        key = os.path.normcase(str(source.resolve()))
        if not claimed:
            with self._active_files_lock:
                if key in self._active_files:
                    return None
                self._active_files.add(key)
        try:
            if not self.wait_until_stable(source):
                if source.exists():
                    self.log(f"Error: file did not finish writing: {source}")
                    return False
                return None
            with self._move_lock:
                if not source.is_file():
                    return None
                destination = move_incoming_file(source, rule)
            self.log(f"Processed: {source.name} -> {destination}")
            return True
        except (OSError, ValueError, KeyError) as error:
            self.log(f"Error processing {source}: {error}")
            return False
        finally:
            with self._active_files_lock:
                self._active_files.discard(key)

    @staticmethod
    def wait_until_stable(source, timeout=300, interval=0.5, stable_checks=5):
        deadline = time.monotonic() + timeout
        previous_size = None
        stable_count = 0
        while time.monotonic() < deadline:
            try:
                stat = source.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                return False
            if signature == previous_size:
                stable_count += 1
                if stable_count >= stable_checks:
                    return True
            else:
                previous_size = signature
                stable_count = 0
            time.sleep(interval)
        return False


class Tool(IngestEngine, BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Auto Ingest"
        self.description = "Rename and route media from multiple watched folders"
        self.category = "Media"
        self.icon = "folder-input"
        self.rules = []
        self.saved_tokens = []
        self.observer = None
        self.is_watching = False
        self.active_rules = []
        self._log_queue = queue.Queue()
        self._init_engine()
        self._batch_running = False
        self._rules_file = RULES_FILE

    def create_window(self, parent):
        window = self.create_tool_window(parent, "Auto Ingest", 1100, 1000)
        window.minsize(920, 760)
        self.window = window

        try:
            self.rules = load_rules(self._rules_file)
            self.saved_tokens = load_saved_tokens(self._rules_file)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.rules = []
            self.saved_tokens = []
            messagebox.showerror(
                "Unable to load watch rules",
                f"Saved watch rules could not be loaded:\n{error}",
                parent=window,
            )

        page = theme.page(window)
        theme.tool_header(
            page, self,
            subtitle="Save a rule for each intake folder. New files are named, dated, and moved to the matching server folder.",
        ).pack(fill="x", pady=(0, 14))

        actions = theme.action_bar(page)
        self.run_now_button = theme.Button(
            actions, "Run Now", kind="secondary", width=120, command=self.run_now,
        )
        self.run_now_button.pack(side="left")
        self.start_button = theme.Button(
            actions, "Start Watching", kind="primary", width=150, command=self.toggle_watching,
        )
        self.start_button.pack(side="right")

        editor_row = ctk.CTkFrame(page, fg_color="transparent")
        editor_row.pack(fill="both", expand=True)
        editor_row.grid_columnconfigure(0, weight=1, minsize=230)
        editor_row.grid_columnconfigure(1, weight=3)
        editor_row.grid_rowconfigure(0, weight=1)

        rules_card = theme.Card(editor_row, title="Watch rules")
        rules_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        rules_body = rules_card.body
        rules_body.grid_columnconfigure(0, weight=1)
        rules_body.grid_rowconfigure(0, weight=1)
        self.rule_list = theme.ThemedListbox(
            rules_body, height=8, selectmode=tk.SINGLE, exportselection=False,
        )
        self.rule_list.grid(row=0, column=0, sticky="nsew")
        self.rule_list.bind("<<ListboxSelect>>", self.on_rule_selected)
        rule_buttons = ctk.CTkFrame(rules_body, fg_color="transparent")
        rule_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        theme.Button(
            rule_buttons, "New Rule", kind="secondary", width=100, command=self.add_rule,
        ).pack(side="left")
        theme.Button(
            rule_buttons, "Remove", kind="ghost", width=86, command=self.remove_rule,
        ).pack(side="right")

        editor_card = theme.Card(
            editor_row,
            title="Rule editor",
            subtitle="Files are watched recursively; their subfolders are preserved after the date folder.",
        )
        editor_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        editor = editor_card.body
        editor.grid_columnconfigure(0, weight=1)

        theme.muted(editor, "Rule name", role="body").grid(row=0, column=0, sticky="w")
        self.rule_name_entry = ctk.CTkEntry(editor, placeholder_text="e.g. Footage Graphics", height=34)
        self.rule_name_entry.grid(row=1, column=0, sticky="ew", pady=(3, 9))
        self.rule_name_entry.bind("<KeyRelease>", self.mark_dirty)

        watch_field = theme.PathField(
            editor, "Watch folder", "Folder where new media arrives", command=self.browse_watch_folder,
        )
        watch_field.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.watch_folder_entry = watch_field.entry
        self.watch_folder_entry.bind("<KeyRelease>", self.mark_dirty)

        destination_field = theme.PathField(
            editor, "Server destination", "Folder where renamed media is moved", command=self.browse_destination,
        )
        destination_field.grid(row=3, column=0, sticky="ew")
        self.destination_entry = destination_field.entry
        self.destination_entry.bind("<KeyRelease>", self.mark_dirty)

        naming_card = theme.Card(
            editor,
            title="Filename builder",
            subtitle="Add and arrange tokens. The original file extension is kept automatically.",
        )
        naming_card.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        naming = naming_card.body
        naming.grid_columnconfigure(0, weight=1)
        self.strip_box = ctk.CTkFrame(
            naming, fg_color=C["field"], border_width=1, border_color=C["border"], corner_radius=8,
        )
        self.strip_box.grid(row=0, column=0, sticky="ew")
        self.strip = TokenFlow(self.strip_box, min_height=34)
        self.strip.pack(fill="x", padx=8, pady=8)
        self.strip.placeholder = theme.muted(
            self.strip, "Empty: drag a token here (the new name would be blank)",
        )
        self.marker = ctk.CTkFrame(
            self.strip, width=3, height=28, corner_radius=1, fg_color=C["accent"],
        )

        palette_row = ctk.CTkFrame(naming, fg_color="transparent")
        palette_row.grid(row=1, column=0, sticky="ew", pady=(9, 0))
        palette_row.grid_columnconfigure(1, weight=1)
        theme.overline(palette_row, "Tokens").grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=(6, 0))
        self.palette = TokenFlow(palette_row, min_height=30)
        self.palette.grid(row=0, column=1, sticky="ew")

        add_row = ctk.CTkFrame(naming, fg_color="transparent")
        add_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        theme.overline(add_row, "Your text").pack(side="left", padx=(0, 10))
        self.custom_token_entry = ctk.CTkEntry(
            add_row, placeholder_text="Custom text", width=150, height=32,
        )
        self.custom_token_entry.pack(side="left")
        self.custom_token_entry.bind("<Return>", lambda _event: self.add_token())
        theme.Button(
            add_row, "Add to Bank", kind="secondary", width=110, command=self.add_custom_token,
        ).pack(side="left", padx=(8, 0))
        self.join_menu = ctk.CTkOptionMenu(
            add_row, values=list(JOINERS), width=96, height=30, command=self.mark_dirty,
        )
        self.join_menu.pack(side="right")
        theme.muted(add_row, "Join tokens with", role="body").pack(side="right", padx=(0, 8))

        date_row = ctk.CTkFrame(naming, fg_color="transparent")
        date_row.grid(row=3, column=0, sticky="ew", pady=(9, 0))
        theme.muted(date_row, "Date folder format", role="body").pack(side="left", padx=(0, 8))
        self.date_format_menu = ctk.CTkOptionMenu(
            date_row, values=list(DATE_FORMATS), width=140, height=32, command=self.mark_dirty,
        )
        self.date_format_menu.pack(side="left")
        theme.muted(
            date_row, "Destination: [date] / [original subfolders] / renamed file", role="caption",
        ).pack(side="left", padx=(12, 0))

        self.remove_path_chars_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            naming,
            text="Remove path-breaking characters from generated filename",
            variable=self.remove_path_chars_var,
            checkbox_width=18,
            checkbox_height=18,
            command=self.mark_dirty,
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.uppercase_original_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            naming,
            text="ALL CAPS original name",
            variable=self.uppercase_original_var,
            checkbox_width=18,
            checkbox_height=18,
            command=self.mark_dirty,
        ).grid(row=5, column=0, sticky="w", pady=(4, 0))
        self.skip_existing_tokens_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            naming,
            text="Skip tokens already present in the original filename",
            variable=self.skip_existing_tokens_var,
            checkbox_width=18,
            checkbox_height=18,
            command=self.mark_dirty,
        ).grid(row=6, column=0, sticky="w", pady=(4, 0))

        preview_frame = ctk.CTkFrame(naming, fg_color="transparent")
        preview_frame.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        self.preview_label = theme.muted(
            preview_frame, "Preview: select or add tokens", role="caption",
        )
        self.preview_label.pack(anchor="w")

        save_row = ctk.CTkFrame(editor, fg_color="transparent")
        save_row.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.rule_status_label = theme.muted(save_row, "Unsaved changes", role="caption")
        self.rule_status_label.pack(side="left")
        theme.Button(
            save_row, "Save Rule", kind="primary", width=110, command=self.save_current_rule,
        ).pack(side="right")

        log_card = theme.Card(page, title="Activity log")
        log_card.pack(fill="x", pady=(12, 0))
        self.status_label = theme.StatusLabel(
            log_card.header, text="Status: Not watching", rules=[("Watching", "success")], height=20,
        )
        self.status_label.pack(side="right")
        self.log_text = theme.LogBox(
            log_card.body, empty_text="Processed files and errors will appear here.", wrap="word", height=100,
        )
        self.log_text.pack(fill="both", expand=True)

        self.refresh_rule_list()
        self.render_palette()
        if self.rules:
            self.rule_list.selection_set(0)
            self.load_rule(self.rules[0])
        else:
            self.load_rule(new_rule(), unsaved=True)
        self._update_watching_ui()
        self.drain_logs()
        return window

    def refresh_rule_list(self, selected_id=None):
        self.rule_list.delete(0, tk.END)
        selected_index = None
        for index, rule in enumerate(self.rules):
            self.rule_list.insert(tk.END, rule.get("name", "Unnamed Rule"))
            if rule.get("id") == selected_id:
                selected_index = index
        if selected_index is not None:
            self.rule_list.selection_set(selected_index)
            self.rule_list.activate(selected_index)

    def on_rule_selected(self, _event=None):
        selection = self.rule_list.curselection()
        if not selection:
            return
        selected = self.rules[selection[0]]
        if selected.get("id") == getattr(self, "_selected_rule_id", None):
            return
        self._pending_selection_id = selected.get("id")
        if not self.confirm_discard_changes():
            current_index = next(
                (i for i, rule in enumerate(self.rules) if rule.get("id") == self._selected_rule_id),
                None,
            )
            if current_index is not None:
                self.rule_list.selection_clear(0, tk.END)
                self.rule_list.selection_set(current_index)
            return
        selected_index = next(
            (i for i, rule in enumerate(self.rules) if rule.get("id") == selected.get("id")),
            None,
        )
        if selected_index is not None:
            self.rule_list.selection_clear(0, tk.END)
            self.rule_list.selection_set(selected_index)
            self.rule_list.activate(selected_index)
        self.load_rule(selected)

    def confirm_discard_changes(self):
        if not getattr(self, "_rule_dirty", False):
            return True
        result = messagebox.askyesnocancel(
            "Unsaved rule changes",
            "Save changes to this rule before switching?",
            parent=self.window,
        )
        if result is None:
            return False
        if result:
            return self.save_current_rule()
        if getattr(self, "_editing_new_rule", False):
            self.rules = [
                rule for rule in self.rules
                if rule.get("id") != self._selected_rule_id
            ]
            self._selected_rule_id = None
            self._editing_new_rule = False
            self.refresh_rule_list(getattr(self, "_pending_selection_id", None))
        return True

    def load_rule(self, rule, unsaved=False):
        self._selected_rule_id = rule["id"]
        self._editing_new_rule = unsaved
        self._layout = [dict(token) for token in rule.get("layout", [{"kind": "original"}])]
        for entry, value in (
            (self.rule_name_entry, rule.get("name", "")),
            (self.watch_folder_entry, rule.get("watch_folder", "")),
            (self.destination_entry, rule.get("destination_folder", "")),
        ):
            entry.delete(0, tk.END)
            entry.insert(0, value)
        self.join_menu.set(rule.get("join", "_"))
        self.date_format_menu.set(rule.get("date_format", "YYMMDD"))
        self.remove_path_chars_var.set(rule.get("remove_path_breaking_chars", False))
        self.uppercase_original_var.set(rule.get("uppercase_original", False))
        self.skip_existing_tokens_var.set(rule.get("skip_existing_tokens", True))
        self.refresh_token_list()
        self.mark_dirty() if unsaved else self.set_clean()

    def add_rule(self):
        if not self.confirm_discard_changes():
            return
        rule = new_rule()
        self.rules.append(rule)
        self.refresh_rule_list(rule["id"])
        self.load_rule(rule, unsaved=True)

    def remove_rule(self):
        selection = self.rule_list.curselection()
        if not selection:
            messagebox.showwarning("No rule selected", "Select a watch rule to remove.", parent=self.window)
            return
        rule = self.rules[selection[0]]
        if not messagebox.askyesno(
            "Remove watch rule", f"Remove the {rule['name']!r} watch rule?", parent=self.window,
        ):
            return
        self.rules.pop(selection[0])
        if not self.persist_rules():
            self.rules.insert(selection[0], rule)
            self.refresh_rule_list(rule["id"])
            return
        self._selected_rule_id = None
        self._editing_new_rule = False
        self.refresh_rule_list()
        if self.rules:
            self.rule_list.selection_set(min(selection[0], len(self.rules) - 1))
            self.load_rule(self.rules[min(selection[0], len(self.rules) - 1)])
        else:
            self.load_rule(new_rule(), unsaved=True)
        if self.is_watching:
            self.rule_status_label.configure(text="Rule removed; restart watcher to apply changes")

    def save_current_rule(self):
        rule_id = getattr(self, "_selected_rule_id", None)
        if not rule_id:
            messagebox.showerror("Unable to save rule", "Add a rule before saving.", parent=self.window)
            return False
        name = self.rule_name_entry.get().strip()
        if not name:
            messagebox.showwarning("Rule name required", "Enter a name for this watch rule.", parent=self.window)
            return False
        if any(
            rule["id"] != rule_id and rule.get("name", "").casefold() == name.casefold()
            for rule in self.rules
        ):
            messagebox.showwarning(
                "Duplicate rule name", "Each watch rule must have a unique name.", parent=self.window,
            )
            return False

        rule = next((item for item in self.rules if item.get("id") == rule_id), None)
        if rule is None:
            rule = {"id": rule_id}
            self.rules.append(rule)
        rule.update({
            "name": name,
            "watch_folder": self.watch_folder_entry.get().strip(),
            "destination_folder": self.destination_entry.get().strip(),
            "layout": [dict(token) for token in self._layout],
            "join": self.join_menu.get(),
            "date_format": self.date_format_menu.get(),
            "remove_path_breaking_chars": self.remove_path_chars_var.get(),
            "uppercase_original": self.uppercase_original_var.get(),
            "skip_existing_tokens": self.skip_existing_tokens_var.get(),
        })
        if not self.persist_rules():
            return False
        self._editing_new_rule = False
        self.refresh_rule_list(rule_id)
        self.set_clean()
        if self.is_watching:
            self.rule_status_label.configure(text="Saved; restart watcher to apply changes")
        return True

    def persist_rules(self):
        try:
            save_rules(self.rules, self._rules_file, saved_tokens=self.saved_tokens)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            messagebox.showerror(
                "Unable to save watch rules", str(error), parent=getattr(self, "window", None),
            )
            return False
        return True

    def set_clean(self):
        self._rule_dirty = False
        if hasattr(self, "rule_status_label"):
            self.rule_status_label.configure(text="Saved")

    def mark_dirty(self, _event=None):
        self._rule_dirty = True
        if hasattr(self, "rule_status_label"):
            self.rule_status_label.configure(text="Unsaved changes")
        self.update_preview()

    def browse_watch_folder(self):
        folder = filedialog.askdirectory(title="Select Watch Folder", parent=self.window)
        if folder:
            self.watch_folder_entry.delete(0, tk.END)
            self.watch_folder_entry.insert(0, folder)
            self.mark_dirty()

    def browse_destination(self):
        folder = filedialog.askdirectory(title="Select Server Destination", parent=self.window)
        if folder:
            self.destination_entry.delete(0, tk.END)
            self.destination_entry.insert(0, folder)
            self.mark_dirty()

    def make_token_chip(self, master, token, palette=False, on_remove=None, layout_index=None):
        builtin = token["kind"] != "text"
        chip = ctk.CTkFrame(
            master,
            corner_radius=12,
            border_width=1,
            fg_color=C["accent_soft"] if builtin else C["control"],
            border_color=C["accent_soft"] if builtin else C["border_strong"],
        )
        label_text = TOKEN_LABELS[token["kind"]] if builtin else chip_label(token)
        label = ctk.CTkLabel(
            chip,
            text=label_text,
            height=24,
            font=theme.font("body_strong") if builtin else theme.font("mono"),
            text_color=C["accent_text"] if builtin else C["text"],
        )
        label.pack(side="left", padx=(10, 2 if on_remove else 10), pady=2)
        if on_remove:
            close = ctk.CTkLabel(
                chip, text="×", width=18, height=24, font=theme.sized_font(15),
                text_color=C["text_muted"], cursor="hand2",
            )
            close.pack(side="left", padx=(0, 6))
            close.bind("<Button-1>", lambda _event: on_remove())
        for widget in (chip, label):
            widget.bind(
                "<ButtonPress-1>",
                lambda event, t=dict(token), c=chip, p=palette, i=layout_index:
                    self.drag_start(event, t, c, p, i),
            )
            widget.bind("<B1-Motion>", self.drag_motion)
            widget.bind("<ButtonRelease-1>", self.drag_end)
            try:
                tk.Misc.configure(widget, cursor="hand2" if palette else "fleur")
            except tk.TclError:
                pass
        return chip

    def render_strip(self):
        chips = [
            self.make_token_chip(self.strip, token, layout_index=index, on_remove=lambda i=index: self.remove_token(i))
            for index, token in enumerate(self._layout)
        ]
        self.strip.set_chips(chips)
        self.update_preview()

    def render_palette(self):
        chips = [
            self.make_token_chip(self.palette, {"kind": kind}, palette=True)
            for kind in ("original", "folder", "date")
        ]
        chips.extend(
            self.make_token_chip(
                self.palette,
                {"kind": "text", "text": text},
                palette=True,
                on_remove=lambda value=text: self.forget_custom_token(value),
            )
            for text in self.saved_tokens
        )
        self.palette.set_chips(chips)

    def refresh_token_list(self):
        self.render_strip()

    def add_token(self):
        self.add_custom_token()

    def add_custom_token(self):
        text = self.custom_token_entry.get()
        if not text:
            return
        token = {"kind": "text", "text": text}
        if text not in self.saved_tokens:
            self.saved_tokens.append(text)
            self.render_palette()
            self.persist_token_bank()
        self.custom_token_entry.delete(0, tk.END)
        self.insert_token(token)

    def persist_token_bank(self):
        try:
            save_rules(self.rules, self._rules_file, saved_tokens=self.saved_tokens)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            messagebox.showerror(
                "Unable to save custom tokens", str(error), parent=getattr(self, "window", None),
            )

    def forget_custom_token(self, text):
        if text not in self.saved_tokens:
            return
        self.saved_tokens.remove(text)
        self.render_palette()
        self.persist_token_bank()

    def remove_token(self, index):
        if 0 <= index < len(self._layout):
            self._layout.pop(index)
            self.render_strip()
            self.mark_dirty()

    def insert_token(self, token, index=None):
        token = dict(token)
        target = len(self._layout) if index is None else max(0, min(index, len(self._layout)))
        self._layout.insert(target, token)
        self.render_strip()
        self.mark_dirty()

    def drag_start(self, event, token, chip, from_palette, index=None):
        self.drag = {
            "token": token,
            "chip": chip,
            "palette": from_palette,
            "index": index,
            "x": event.x_root,
            "y": event.y_root,
            "moving": False,
            "ghost": None,
        }

    def drag_motion(self, event):
        drag = getattr(self, "drag", None)
        if not drag:
            return
        if not drag["moving"]:
            if abs(event.x_root - drag["x"]) + abs(event.y_root - drag["y"]) < 5:
                return
            drag["moving"] = True
            ghost = tk.Toplevel(self.window)
            ghost.overrideredirect(True)
            try:
                ghost.attributes("-topmost", True)
                ghost.attributes("-alpha", 0.85)
            except tk.TclError:
                pass
            index = theme.mode_index()
            tk.Label(
                ghost,
                text=TOKEN_LABELS[drag["token"]["kind"]] if drag["token"]["kind"] != "text"
                else chip_label(drag["token"]),
                bg=C["accent"][index],
                fg="#FFFFFF",
                padx=10,
                pady=3,
                font=(theme.font("body_strong").cget("family"), 11, "bold"),
            ).pack()
            drag["ghost"] = ghost
            if not drag["palette"]:
                drag["chip"].configure(fg_color=C["hover"], border_color=C["accent"])
        drag["ghost"].geometry(f"+{event.x_root + 12}+{event.y_root + 10}")
        if self.strip.contains(event.x_root, event.y_root):
            index = self.strip.index_at(event.x_root, event.y_root)
            x, y, height = self.strip.marker_position(index)
            scale = ctk.ScalingTracker.get_widget_scaling(self.strip)
            self.marker.place(x=x / scale, y=y / scale)
            self.marker.configure(height=max(height, 20) / scale)
            self.marker.lift()
        else:
            self.marker.place_forget()

    def drag_end(self, event):
        drag, self.drag = getattr(self, "drag", None), None
        self.marker.place_forget()
        if not drag:
            return
        if drag["ghost"] is not None:
            drag["ghost"].destroy()
        if not drag["moving"]:
            if drag["palette"]:
                self.insert_token(drag["token"])
            return
        if not self.strip.contains(event.x_root, event.y_root):
            self.render_strip()
            return
        index = self.strip.index_at(event.x_root, event.y_root)
        if drag["palette"]:
            self.insert_token(drag["token"], index)
            return
        old = drag["index"]
        if old is None:
            return
        token = self._layout.pop(old)
        if index > old:
            index -= 1
        self._layout.insert(index, token)
        self.render_strip()
        self.mark_dirty()

    def update_preview(self):
        if not hasattr(self, "preview_label"):
            return
        watch_text = self.watch_folder_entry.get() if hasattr(self, "watch_folder_entry") else ""
        values = {
            "original": clean_original_name(
                "Example clip",
                self.remove_path_chars_var.get() if hasattr(self, "remove_path_chars_var") else False,
                self.uppercase_original_var.get() if hasattr(self, "uppercase_original_var") else False,
            ),
            "folder": Path(watch_text).name if watch_text else "Watch Folder",
            "date": datetime.now().strftime(DATE_FORMATS.get(
                self.date_format_menu.get(), DATE_FORMATS["YYMMDD"]
            )) if hasattr(self, "date_format_menu") else datetime.now().strftime("%y%m%d"),
        }
        parts = [
            token.get("text", "") if token.get("kind") == "text"
            else values.get(token.get("kind"), "")
            for token in getattr(self, "_layout", [])
        ]
        if getattr(self, "skip_existing_tokens_var", None) and self.skip_existing_tokens_var.get():
            original = values["original"].casefold()
            parts = [
                part for token, part in zip(getattr(self, "_layout", []), parts)
                if token.get("kind") == "original"
                or not part
                or part.casefold() not in original
            ]
        joiner = JOINERS.get(self.join_menu.get(), "_") if hasattr(self, "join_menu") else "_"
        stem = smart_join(parts, joiner)
        if getattr(self, "remove_path_chars_var", None) and self.remove_path_chars_var.get():
            stem = sanitize_path_breaking_chars(stem)
        self.preview_label.configure(text=f"Preview: {stem}.mov")

    def validate_for_run(self):
        if getattr(self, "_rule_dirty", False) and not self.save_current_rule():
            return None
        if not self.rules:
            messagebox.showerror(
                "No watch rules", "Add and save at least one watch rule before running.",
                parent=self.window,
            )
            return None
        errors = validate_rules(self.rules)
        if errors:
            messagebox.showerror(
                "Watch rules need attention", "\n".join(f"• {error}" for error in errors),
                parent=self.window,
            )
            return None
        return [dict(rule, layout=[dict(token) for token in rule["layout"]]) for rule in self.rules]

    def toggle_watching(self):
        if self.is_watching:
            self.stop_watching()
            return
        if background_services.agent_has("auto_ingest"):
            background_services.set_service("auto_ingest", False)
            self.log("Stopped watching in the background.")
            self._update_watching_ui()
            return
        rules = self.validate_for_run()
        if rules is None:
            return
        try:
            observer = self.start_observer(rules)
        except (OSError, RuntimeError) as error:
            messagebox.showerror("Unable to start watching", str(error), parent=self.window)
            return
        background_services.set_service("auto_ingest", False)
        self.observer = observer
        self.active_rules = rules
        self.is_watching = True
        self.log(f"Watching {len(rules)} rule(s). If you close Auto AE, watching carries on from the tray icon.")
        self._update_watching_ui()

    def stop_watching(self):
        background_services.set_service("auto_ingest", False)
        observer, self.observer = self.observer, None
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)
            if observer.is_alive():
                self.observer = observer
                messagebox.showerror(
                    "Unable to stop watcher",
                    "The file watcher did not stop within 5 seconds.",
                    parent=getattr(self, "window", None),
                )
                return
        self.is_watching = False
        self.active_rules = []
        self.log("Stopped watching.")
        self._update_watching_ui()

    def close_app(self):
        was_watching = self.is_watching
        if self.observer is not None:
            observer, self.observer = self.observer, None
            observer.stop()
            observer.join(timeout=5)
            if observer.is_alive():
                raise RuntimeError("Auto Ingest did not stop within 5 seconds.")
        self.is_watching = False
        if was_watching:
            background_services.handover("auto_ingest")

    def _update_watching_ui(self):
        if not hasattr(self, "start_button"):
            return
        if self.is_watching:
            self.start_button.configure(text="Stop Watching")
            self.status_label.configure(text="Status: Watching")
        elif background_services.agent_has("auto_ingest"):
            self.start_button.configure(text="Stop Watching")
            self.status_label.configure(text="Status: Watching in the background (tray icon)")
        else:
            self.start_button.configure(text="Start Watching")
            self.status_label.configure(text="Status: Not watching")

    def run_now(self):
        if self._batch_running:
            messagebox.showinfo("Run Now", "A one-time scan is already running.", parent=self.window)
            return
        rules = self.validate_for_run()
        if rules is None:
            return
        self._batch_running = True
        self.log(f"Run Now started: scanning {len(rules)} rule(s).")
        threading.Thread(
            target=self._run_now_worker, args=(rules,), daemon=True,
        ).start()

    def _run_now_worker(self, rules):
        processed = 0
        failed = 0
        try:
            for rule in rules:
                try:
                    files = sorted(
                        (path for path in Path(rule["watch_folder"]).rglob("*") if path.is_file()),
                        key=lambda path: str(path).casefold(),
                    )
                except OSError as error:
                    failed += 1
                    self.log(f"Error scanning {rule['watch_folder']}: {error}")
                    continue
                self.log(f"{rule['name']}: found {len(files)} file(s).")
                for source in files:
                    result = self.process_stable_file(source, rule)
                    if result is True:
                        processed += 1
                    elif result is False:
                        failed += 1
            self.log(f"Run Now finished: {processed} file(s) moved, {failed} error(s).")
        finally:
            self._batch_running = False

    def log(self, message):
        self._log_queue.put(f"[{datetime.now():%H:%M:%S}] {message}")

    def drain_logs(self):
        if not hasattr(self, "log_text"):
            return
        while True:
            try:
                line = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
        if self.window.winfo_exists():
            self.window.after(200, self.drain_logs)
