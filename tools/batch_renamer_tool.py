import errno
import json
import os
import re
import sys
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

import error_help
import theme
from base_tool import BaseTool
from theme import C

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS_FILE = os.path.join(APP_DIR, "batch_renamer_tokens.json")

MODES = ["String Substitution", "Text", "Number Counter", "Prefix", "Postfix", "Date", "Sequence Number"]
DATE_FORMATS = {"YYYYMMDD": "%Y%m%d", "YYMMDD": "%y%m%d", "MMDDYY": "%m%d%y",
                "YYYY-MM-DD": "%Y-%m-%d", "MM-DD-YYYY": "%m-%d-%Y", "DD-MM-YYYY": "%d-%m-%Y",
                "YYYY.MM.DD": "%Y.%m.%d"}
SEQ_ACTIONS = ["Remove", "Renumber", "Shift by", "Set digits"]
SEQ_ORDERS = ["By number", "List order"]
DIGIT_CHOICES = ["Auto", "1", "2", "3", "4", "5", "6", "7", "8"]
SEPARATORS = "_-. "

# Built-in tokens for the name builder: kind -> (chip label, what it inserts)
BUILTIN_TOKENS = {
    "name": ("Name", "The file name after the rules above"),
    "number": ("Number", "The file's sequence number (e.g. 0001), even if a rule removed it from Name"),
    "counter": ("Counter", "1, 2, 3... in list order"),
    "date": ("Date", "Today's date (format below)"),
    "folder": ("Folder", "The name of the folder the file is in"),
    "original": ("Original", "The original file name, before any rules"),
}
JOINERS = {"_": "_", "-": "-", ".": ".", "Space": " ", "Nothing": ""}


# --------------------------------------------------------------------------- naming engine
def natural_key(text):
    """Sort 'clip2' before 'clip10'."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


def find_sequence(stem, need_separator=True):
    """Split a trailing number off a name: 'clip_0001' -> ('clip', '_', '0001').

    With need_separator, the number must follow _ - . or a space (or be the whole name), so
    names such as 'A002C068_LAJ05' keep their '05'. Returns None when there is no number.
    """
    match = re.match(r"^(.*?)(\d+)$", stem)
    if not match:
        return None
    head, number = match.groups()
    separator = head[-1] if head and head[-1] in SEPARATORS else ""
    if need_separator and head and not separator:
        return None
    return (head[:-1] if separator else head), separator, number


def _int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _pad(number, digits, original_width):
    """Zero-pad `number`. digits 'Auto' keeps the width the number had before."""
    width = original_width if str(digits) == "Auto" else _int(digits, original_width)
    text = str(abs(number)).zfill(width)
    return ("-" + text) if number < 0 else text


def _split(name, affect_extension):
    return (name, "") if affect_extension else os.path.splitext(name)


def apply_rules(files, rules, affect_extension=False, today=None):
    """Run the rename rules over `files` (full paths).

    Returns (names, numbers, notes): the new file names, each file's sequence number (for the
    builder's Number token) and per-file notes such as 'No sequence number found'.
    """
    today = today or datetime.now()
    names = [os.path.basename(path) for path in files]
    numbers = [None] * len(files)
    notes = [[] for _ in files]
    for original_index, name in enumerate(names):          # default Number: from the original name
        found = find_sequence(os.path.splitext(name)[0])
        numbers[original_index] = found[2] if found else None

    for rule in rules:
        mode = rule.get("mode")
        if mode == "String Substitution":
            find, replace = rule.get("find", ""), rule.get("replace", "")
            if not find:
                continue
            for i, name in enumerate(names):
                stem, ext = _split(name, affect_extension)
                names[i] = stem.replace(find, replace) + ext

        elif mode in ("Text", "Prefix"):
            text = rule.get("text", "")
            names = [text + name for name in names]

        elif mode == "Postfix":
            text = rule.get("text", "")
            for i, name in enumerate(names):
                stem, ext = _split(name, affect_extension)
                names[i] = stem + text + ext

        elif mode == "Date":
            fmt = rule.get("date_format", "YYYY-MM-DD")
            pattern = rule.get("custom") if fmt == "Custom" else DATE_FORMATS.get(fmt, "%Y-%m-%d")
            try:
                stamp = today.strftime(pattern or "%Y-%m-%d")
            except ValueError:
                stamp = today.strftime("%Y-%m-%d")
            for i, name in enumerate(names):
                stem, ext = _split(name, affect_extension)
                names[i] = f"{stem}_{stamp}{ext}"

        elif mode == "Number Counter":
            start_text = str(rule.get("start", "")).strip()
            try:
                start, step = int(start_text), int(str(rule.get("increment", "")).strip())
            except ValueError:
                continue
            width = len(start_text.lstrip("-")) if start_text.lstrip("-").isdigit() else 0
            for i, name in enumerate(names):
                value = start + step * i
                text = str(value).zfill(width) if width > 1 else str(value)
                stem, ext = _split(name, affect_extension)
                names[i] = f"{stem}_{text}{ext}"

        elif mode == "Sequence Number":
            _apply_sequence(names, numbers, notes, rule)
    return names, numbers, notes


def _apply_sequence(names, numbers, notes, rule):
    action = rule.get("action", "Remove")
    need_separator = rule.get("need_separator", True)
    found = []
    for name in names:
        stem, ext = os.path.splitext(name)
        found.append((find_sequence(stem, need_separator), ext))

    ranks = {}
    if action == "Renumber":            # each run of names (Title_GFX_..., Lower3rd-...) counts on its own
        groups = {}
        for i, (hit, ext) in enumerate(found):
            if hit:
                groups.setdefault((hit[0].casefold(), ext.casefold()), []).append(i)
        for members in groups.values():
            if rule.get("order", SEQ_ORDERS[0]) == SEQ_ORDERS[0]:
                members.sort(key=lambda i: (int(found[i][0][2]), i))
            ranks.update({i: rank for rank, i in enumerate(members)})

    start, step = _int(rule.get("start"), 1), _int(rule.get("step"), 1)
    offset = _int(rule.get("offset"), 0)
    digits = rule.get("digits", "Auto")
    for i, (hit, ext) in enumerate(found):
        if not hit:
            notes[i].append("No sequence number found")
            continue
        head, separator, number = hit
        if action == "Remove":
            names[i] = head + ext
            numbers[i] = number
            continue
        if action == "Renumber":
            value = start + step * ranks[i]
        elif action == "Shift by":
            value = int(number) + offset
        else:                                    # Set digits
            value = int(number)
        new_number = _pad(value, digits, len(number))
        names[i] = f"{head}{separator}{new_number}{ext}"
        numbers[i] = new_number


def smart_join(parts, joiner):
    """Join parts, without doubling the joiner when a part already starts or ends with it."""
    result = ""
    for part in parts:
        if not part:
            continue
        if result and joiner:
            if result.endswith(joiner) and part.startswith(joiner):
                part = part[len(joiner):]
            elif not result.endswith(joiner) and not part.startswith(joiner):
                result += joiner
        result += part
    if joiner:          # an empty token at either end shouldn't leave a stray "_"
        while result.startswith(joiner):
            result = result[len(joiner):]
        while result.endswith(joiner):
            result = result[:-len(joiner)]
    return result


def build_names(files, names, numbers, layout, settings, today=None):
    """Lay each name out from the builder's tokens. The extension is kept as it is."""
    if not layout or layout == [{"kind": "name"}]:
        return list(names)
    today = today or datetime.now()
    joiner = JOINERS.get(settings.get("join", "_"), "_")
    start, step = _int(settings.get("counter_start"), 1), _int(settings.get("counter_step"), 1)
    counter_digits = settings.get("counter_digits", "4")
    date_text = today.strftime(DATE_FORMATS.get(settings.get("date_format", "YYYYMMDD"), "%Y%m%d"))
    built = []
    for i, (path, name) in enumerate(zip(files, names)):
        stem, ext = os.path.splitext(name)
        values = {
            "name": stem,
            "number": numbers[i] or "",
            "counter": _pad(start + step * i, counter_digits, 1),
            "date": date_text,
            "folder": os.path.basename(os.path.dirname(path)),
            "original": os.path.splitext(os.path.basename(path))[0],
        }
        parts = [token.get("text", "") if token["kind"] == "text" else values.get(token["kind"], "")
                 for token in layout]
        built.append(smart_join(parts, joiner) + ext)
    return built


def check_names(files, new_names, notes=None):
    """One status per file: (text, level). Levels: ok, same, note, conflict."""
    fold = (lambda s: s.casefold()) if os.name == "nt" or sys.platform == "darwin" else (lambda s: s)
    targets = {}
    for path, new in zip(files, new_names):
        targets.setdefault(fold(os.path.join(os.path.dirname(path), new)), []).append(path)
    sources = {fold(path) for path in files}
    results = []
    for i, (path, new) in enumerate(zip(files, new_names)):
        old = os.path.basename(path)
        stem = os.path.splitext(new)[0] if new else ""
        target = os.path.join(os.path.dirname(path), new)
        if not new or not stem.strip():
            results.append(("Name would be empty", "conflict"))
            continue
        if os.name == "nt":
            problems = error_help.name_problems(new)
        else:
            problems = [f"contains {c!r}" for c in "/:\0" if c in new]
        if problems:
            results.append(("Bad name: " + problems[0], "conflict"))
            continue
        if len(targets[fold(target)]) > 1:
            others = [os.path.basename(p) for p in targets[fold(target)] if p != path]
            results.append((f"Same new name as {others[0]}", "conflict"))
            continue
        if new == old:
            note = (notes[i][0] if notes and notes[i] else "Unchanged")
            results.append((note, "same"))
            continue
        if os.path.exists(target) and fold(target) not in sources:
            results.append(("A file with this name already exists", "conflict"))
            continue
        if notes and notes[i]:
            results.append((notes[i][0], "note"))
        else:
            results.append(("", "ok"))
    return results


def rename_all(pairs):
    """Rename [(old_path, new_path)] safely, even when names swap (0001 <-> 0002).

    Every file first moves to a temporary name, then to its new name. On any error, everything
    done so far is put back and the error is raised. Returns the list of pairs that were renamed.
    """
    pairs = [(old, new) for old, new in pairs if old != new]
    staged, done = [], []
    try:
        for n, (old, new) in enumerate(pairs):
            temporary = os.path.join(os.path.dirname(old), f".autoae-rename-{os.getpid()}-{n}.tmp")
            os.rename(old, temporary)
            staged.append((old, temporary, new))
        for old, temporary, new in staged:
            if os.path.exists(new):
                raise FileExistsError(errno.EEXIST, "A file with this name already exists", new)
            os.rename(temporary, new)
            done.append((old, temporary, new))
    except Exception:
        for old, temporary, new in reversed(done):
            try:
                os.rename(new, temporary)
            except OSError:
                pass
        for old, temporary, new in reversed(staged):
            try:
                os.rename(temporary, old)
            except OSError:
                pass
        raise
    return [(old, new) for old, _, new in staged]


# --------------------------------------------------------------------------- token chips
class TokenFlow(ctk.CTkFrame):
    """Chips laid out left to right, wrapping to new lines. Used for the name and the palette."""

    GAP = 6

    def __init__(self, master, min_height=40, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, height=min_height, **kwargs)
        self.min_height = min_height
        self.chips = []
        self.positions = []
        self.placeholder = None
        self.bind("<Configure>", lambda e: self.after_idle(self.layout), add="+")

    def set_chips(self, chips):
        for chip in self.chips:
            if chip not in chips:
                chip.destroy()
        self.chips = chips
        self.after_idle(self.layout)

    def layout(self):
        if not self.winfo_exists():
            return
        self.update_idletasks()
        width = max(self.winfo_width(), 100)
        x = y = row_height = 0
        self.positions = []
        for chip in self.chips:
            w, h = chip.winfo_reqwidth(), chip.winfo_reqheight()
            if x and x + w > width:
                x, y, row_height = 0, y + row_height + self.GAP, 0
            self.positions.append((x, y, w, h))
            x += w + self.GAP
            row_height = max(row_height, h)
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        for chip, (x, y, w, h) in zip(self.chips, self.positions):
            chip.place(x=x / scale, y=y / scale)       # CTk scales place() coordinates itself
        if self.placeholder is not None:
            if self.chips:
                self.placeholder.place_forget()
            else:
                self.placeholder.place(x=4, y=4)
        wanted = max(self.min_height * scale, y + row_height)
        if abs(self.winfo_height() - wanted) > 1:
            self.configure(height=wanted / scale)

    def index_at(self, x_root, y_root):
        """Where a chip dropped at this screen point would go."""
        lx, ly = x_root - self.winfo_rootx(), y_root - self.winfo_rooty()
        for i, (x, y, w, h) in enumerate(self.positions):
            if ly < y or (ly < y + h + self.GAP and lx < x + w / 2):
                return i
        return len(self.positions)

    def contains(self, x_root, y_root, margin=12):
        x, y = self.winfo_rootx(), self.winfo_rooty()
        return (x - margin <= x_root <= x + self.winfo_width() + margin
                and y - margin <= y_root <= y + self.winfo_height() + margin)

    def marker_position(self, index):
        if not self.positions:
            return 0, 0, 28
        if index < len(self.positions):
            x, y, w, h = self.positions[index]
            return max(0, x - self.GAP // 2 - 1), y, h
        x, y, w, h = self.positions[-1]
        return x + w + self.GAP // 2 - 1, y, h


def chip_label(token):
    if token["kind"] == "text":
        text = token.get("text", "")
        return text.replace(" ", "·") if text.strip() else "·" * max(1, len(text))
    return BUILTIN_TOKENS[token["kind"]][0]


# --------------------------------------------------------------------------- the tool
class _FilesView:
    """Old code and tests put names into `files_listbox`; the file table is drawn from
    `selected_files`, so this just redraws it."""

    def __init__(self, tool):
        self.tool = tool

    def insert(self, index, *items):
        self.tool.refresh_table()

    def delete(self, *args):
        pass

    def bind(self, *args, **kwargs):
        pass


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Batch Renamer"
        self.description = "Rename multiple files at once with various options"
        self.category = "Media"
        self.icon = "pencil-line"
        self.selected_files = []
        self.text_option_vars = []
        self.text_option_widgets = []
        self.text_option_start_row = 3  # Row offset for dynamic text options
        self.layout = [{"kind": "name"}]
        self.saved_tokens = self.load_saved_tokens()
        self.undo_pairs = []
        self.drag = None
        self._preview_job = None
        self.rows = []

    # ----------------------------------------------------------------- window
    def create_window(self, parent):
        window = self.create_tool_window(parent, "Batch Renamer", 1000, 900)   # fit_window grows it
        self.window = window
        self._ready = False                 # no previews until every control exists
        self.rule_rows_frame = None
        self.text_option_vars, self.text_option_widgets = [], []

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 14))

        # --- Actions ---------------------------------------------------------
        actions = theme.action_bar(page)
        self.file_count_label = theme.muted(actions, text="0 files will be processed", role="body")
        self.file_count_label.pack(side="left")
        rename_button = theme.Button(actions, "Rename Files", kind="primary", width=140, command=self.rename_files)
        rename_button.pack(side="right")
        self.undo_button = theme.Button(actions, "Undo Rename", kind="secondary", width=120, state="disabled",
                                        command=self.undo_rename)
        self.undo_button.pack(side="right", padx=(0, 8))

        # --- Files + live preview --------------------------------------------
        files_card = theme.Card(page, title="Files")
        files_card.pack(fill="both", expand=True)
        self.summary_label = ctk.CTkLabel(files_card.header, text="", font=theme.font("caption"),
                                          text_color=C["text_muted"], height=20)
        self.summary_label.pack(side="right")
        body = files_card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        theme.style_ttk(window)
        self.tree = ttk.Treeview(body, columns=("current", "new", "status"), show="headings",
                                 selectmode="extended", height=3)
        for column, heading, width, stretch in (("current", "Current name", 280, True),
                                                ("new", "New name", 300, True),
                                                ("status", "", 190, False)):
            self.tree.heading(column, text=heading, anchor="w")
            self.tree.column(column, width=width, minwidth=90, stretch=stretch, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        theme.add_scrollbar(self.tree, body, row=0, column=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_preview())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<BackSpace>", lambda e: self.remove_selected())
        self.files_listbox = _FilesView(self)
        ctk.AppearanceModeTracker.add(self._retint_table, self.tree)
        self.tree.bind("<Destroy>", lambda e: e.widget is self.tree and
                       ctk.AppearanceModeTracker.remove(self._retint_table), add="+")

        detail = ctk.CTkFrame(body, fg_color="transparent")
        detail.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        detail.grid_columnconfigure(0, weight=1)
        self.current_preview = ctk.CTkLabel(detail, text="Current Filename: ", anchor="w",
                                            text_color=C["text_muted"], height=20)
        self.current_preview.grid(row=0, column=0, sticky="ew")
        self.new_preview = ctk.CTkLabel(detail, text="New Filename: ", anchor="w",
                                        font=theme.font("body_strong"), height=20)
        self.new_preview.grid(row=1, column=0, sticky="ew")

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        theme.Button(buttons, "Add Files…", kind="secondary", width=110, command=self.open_files).pack(side="left")
        theme.Button(buttons, "Add Folder…", kind="secondary", width=110,
                     command=self.open_folder).pack(side="left", padx=(8, 0))
        theme.Button(buttons, "Remove Selected", kind="ghost", width=130,
                     command=self.remove_selected).pack(side="left", padx=(8, 0))
        theme.Button(buttons, "Clear", kind="ghost", width=76, command=self.clear_all).pack(side="left", padx=(4, 0))

        # --- Rules ---------------------------------------------------------------
        rules_card = theme.Card(page, title="Rename rules",
                                subtitle="Rules run top to bottom. Sequence Number removes or renumbers "
                                         "trailing numbers like _0001.")
        rules_card.pack(fill="x", pady=(12, 0))
        filenames_frame = rules_card.body
        self.filenames_frame = filenames_frame
        filenames_frame.grid_columnconfigure(2, weight=1)

        theme.muted(filenames_frame, "Start from", role="body").grid(row=0, column=0, padx=(0, 12),
                                                                     pady=(0, 6), sticky="w")
        self.ogfilename_var = tk.StringVar(value="Original Filename")
        self.original_filename_dropdown = ctk.CTkOptionMenu(
            filenames_frame, variable=self.ogfilename_var,
            values=["Original Filename", "Intermediate Filename"], width=200, height=32)
        self.original_filename_dropdown.grid(row=0, column=1, pady=(0, 6), sticky="w")
        self.affect_extension_checkbox = ctk.CTkCheckBox(
            filenames_frame, text="Affect Extension", checkbox_width=18, checkbox_height=18,
            command=self.update_preview)
        self.affect_extension_checkbox.grid(row=0, column=2, padx=(16, 0), pady=(0, 6), sticky="w")

        rule_buttons = ctk.CTkFrame(filenames_frame, fg_color="transparent")
        rule_buttons.grid(row=0, column=3, pady=(0, 6), sticky="e")
        theme.Button(rule_buttons, "Add Rule", kind="secondary", width=100, height=32,
                     image=theme.icon("plus", C["text"], 15), compound="left",
                     command=self.add_text_option).pack(side="left")
        theme.Button(rule_buttons, "Remove Last", kind="ghost", width=100, height=32,
                     command=self.remove_text_option).pack(side="left", padx=(6, 0))

        self.rule_rows_frame = ctk.CTkFrame(filenames_frame, fg_color="transparent")
        self.rule_rows_frame.grid(row=1, column=0, columnspan=4, sticky="ew")
        self.add_text_option()

        # --- Name builder ------------------------------------------------------
        builder = theme.Card(page, title="Name builder",
                             subtitle="Drag tokens to arrange the new name. Click or drag a token below to add it. "
                                      "× removes; double-click your own text to edit it.")
        builder.pack(fill="x", pady=(12, 0))
        theme.Button(builder.header, "Reset", kind="ghost", width=60, height=20,
                     command=self.reset_layout).pack(side="right")
        self.hint_label = ctk.CTkLabel(builder.header, text="", font=theme.font("caption"),
                                       text_color=C["accent_text"], height=20)
        self.hint_label.pack(side="right", padx=(0, 12))
        bbody = builder.body
        bbody.grid_columnconfigure(0, weight=1)

        self.strip_box = ctk.CTkFrame(bbody, fg_color=C["field"], border_width=1, border_color=C["border"],
                                      corner_radius=8)
        self.strip_box.grid(row=0, column=0, sticky="ew")
        self.strip = TokenFlow(self.strip_box, min_height=32)
        self.strip.pack(fill="x", padx=8, pady=8)
        self.strip.placeholder = theme.muted(self.strip, "Empty: drag a token here (the new name would be blank)")
        self.marker = ctk.CTkFrame(self.strip, width=3, height=28, corner_radius=1, fg_color=C["accent"])

        palette_row = ctk.CTkFrame(bbody, fg_color="transparent")
        palette_row.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        palette_row.grid_columnconfigure(1, weight=1)
        theme.overline(palette_row, "Tokens").grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=(6, 0))
        self.palette = TokenFlow(palette_row, min_height=30)
        self.palette.grid(row=0, column=1, sticky="ew")

        custom_row = ctk.CTkFrame(bbody, fg_color="transparent")
        custom_row.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        theme.overline(custom_row, "Your text").pack(side="left", padx=(0, 10))
        self.custom_entry = ctk.CTkEntry(custom_row, placeholder_text="e.g. _GFX_", width=150, height=30)
        self.custom_entry.pack(side="left")
        self.custom_entry.bind("<Return>", lambda e: self.add_custom_token())
        theme.Button(custom_row, "Add Token", kind="secondary", width=100, height=30,
                     command=self.add_custom_token).pack(side="left", padx=(8, 0))

        self.join_var = tk.StringVar(value="_")
        self.counter_start_var = tk.StringVar(value="1")
        self.counter_step_var = tk.StringVar(value="1")
        self.counter_digits_var = tk.StringVar(value="4")
        self.date_format_var = tk.StringVar(value="YYYYMMDD")
        ctk.CTkOptionMenu(custom_row, variable=self.join_var, values=list(JOINERS), width=96, height=30,
                          command=lambda _: self.update_preview()).pack(side="right")
        theme.muted(custom_row, "Join tokens with", role="body").pack(side="right", padx=(0, 8))

        # Counter / Date settings only show while those tokens are in the name
        self.token_settings = ctk.CTkFrame(bbody, fg_color="transparent")
        self.counter_settings = ctk.CTkFrame(self.token_settings, fg_color="transparent")
        theme.muted(self.counter_settings, "Counter from", role="body").pack(side="left", padx=(0, 6))
        ctk.CTkEntry(self.counter_settings, textvariable=self.counter_start_var, width=56, height=30).pack(side="left")
        theme.muted(self.counter_settings, "step", role="body").pack(side="left", padx=(8, 6))
        ctk.CTkEntry(self.counter_settings, textvariable=self.counter_step_var, width=44, height=30).pack(side="left")
        theme.muted(self.counter_settings, "digits", role="body").pack(side="left", padx=(8, 6))
        ctk.CTkOptionMenu(self.counter_settings, variable=self.counter_digits_var, values=DIGIT_CHOICES[1:],
                          width=64, height=30, command=lambda _: self.update_preview()).pack(side="left")
        self.date_settings = ctk.CTkFrame(self.token_settings, fg_color="transparent")
        theme.muted(self.date_settings, "Date format", role="body").pack(side="left", padx=(0, 6))
        ctk.CTkOptionMenu(self.date_settings, variable=self.date_format_var, values=list(DATE_FORMATS),
                          width=120, height=30, command=lambda _: self.update_preview()).pack(side="left")
        for var in (self.counter_start_var, self.counter_step_var):
            var.trace_add("write", lambda *a: self.update_preview())

        # Pack order decides who shrinks when the window is short: the file table gives way first
        for card in (files_card, rules_card, builder):
            card.pack_forget()
        builder.pack(side="bottom", fill="x", pady=(12, 0))
        rules_card.pack(side="bottom", fill="x", pady=(12, 0))
        files_card.pack(fill="both", expand=True)

        self._ready = True
        self.render_strip()
        self.render_palette()
        self.refresh_table()
        self.fit_window()
        return window

    def fit_window(self):
        """Grow the window (and its minimum size) to fit everything, so the file table and rule
        rows never get squashed. Kept on screen; the window never shrinks on its own."""
        if not getattr(self, "_ready", False) or not self.window.winfo_exists():
            return
        window = self.window
        if not window.winfo_viewable():         # sizes are meaningless until it's on screen
            window.after(50, self.fit_window)
            return
        window.update_idletasks()
        scale = ctk.ScalingTracker.get_window_scaling(window)
        screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
        need_w = min(window.winfo_reqwidth(), screen_w * 0.95)
        need_h = min(window.winfo_reqheight(), screen_h * 0.9)
        window.minsize(int(need_w / scale), int(need_h / scale))    # CTk scales minsize itself
        width, height = window.winfo_width(), window.winfo_height()
        if width >= need_w and height >= need_h:
            return
        width, height = max(width, need_w), max(height, need_h)
        x = min(window.winfo_x(), max(0, int(screen_w - width)))
        y = min(window.winfo_y(), max(0, int(screen_h * 0.95 - height)))
        window.geometry("{}x{}+{}+{}".format(int(width / scale), int(height / scale), x, y))

    # ----------------------------------------------------------------- rules
    def add_text_option(self):
        """Add a rule row: a mode menu plus the options for that mode."""
        new_var = tk.StringVar(value="Select Mode")
        self.text_option_vars.append(new_var)
        master = self.rule_rows_frame or self.filenames_frame
        mode_frame = ctk.CTkFrame(master, fg_color=C["surface_2"], corner_radius=8)
        mode_frame.pack(fill="x", pady=(6, 0))

        new_mode_dropdown = ctk.CTkOptionMenu(mode_frame, variable=new_var, values=MODES, width=170)
        new_mode_dropdown.grid(row=0, column=0, padx=(8, 8), pady=6)
        options = ctk.CTkFrame(mode_frame, fg_color="transparent", width=1, height=1)  # empty until a mode is picked
        options.grid(row=0, column=1, sticky="w")
        mode_frame.grid_columnconfigure(1, weight=1)
        item = {"frame": mode_frame, "var": new_var, "widgets": {}}
        close = ctk.CTkButton(mode_frame, text="×", width=28, height=28, fg_color="transparent",
                              hover_color=C["hover"], text_color=C["text_muted"], font=theme.sized_font(16),
                              command=lambda: self.remove_rule(item))
        close.grid(row=0, column=2, padx=(4, 6))

        widgets = {"find": None, "replace": None, "text_entry": None, "date_dropdown": None,
                   "custom_date": None, "counter_start": None, "counter_increment": None,
                   "affect_extension": None, "seq_action": None, "seq_start": None, "seq_step": None,
                   "seq_offset": None, "seq_digits": None, "seq_order": None, "seq_need_sep": None}
        item["widgets"] = widgets

        def entry(key, placeholder, width=140, column=0, value=None):
            widget = ctk.CTkEntry(options, placeholder_text=placeholder, width=width)
            if value is not None:
                widget.insert(0, value)
            widget.grid(row=0, column=column, padx=4)
            widget.bind("<KeyRelease>", lambda event: self.update_preview())
            widgets[key] = widget
            return widget

        def menu(key, values, value, width=130, column=0, command=None):
            var = tk.StringVar(value=value)
            widget = ctk.CTkOptionMenu(options, values=values, variable=var, width=width,
                                       command=command or (lambda _: self.update_preview()))
            widget.grid(row=0, column=column, padx=4)
            widgets[key] = widget
            return widget

        def label(text, column):
            theme.muted(options, text, role="body").grid(row=0, column=column, padx=(8, 0))

        def show_sequence_options(action):
            for child in options.winfo_children()[1:]:
                child.destroy()
            for key in ("seq_start", "seq_step", "seq_offset", "seq_digits", "seq_order", "seq_need_sep"):
                widgets[key] = None
            column = 1
            if action == "Renumber":
                label("from", column); entry("seq_start", "1", 56, column + 1, "1")
                label("step", column + 2); entry("seq_step", "1", 44, column + 3, "1")
                column += 4
            elif action == "Shift by":
                entry("seq_offset", "+/-", 64, column, "0")
                column += 1
            if action != "Remove":
                label("digits", column); menu("seq_digits", DIGIT_CHOICES, "Auto", 72, column + 1)
                column += 2
            if action == "Renumber":
                menu("seq_order", SEQ_ORDERS, SEQ_ORDERS[0], 110, column)
                column += 1
            box = ctk.CTkCheckBox(options, text="Needs _ - . before it", checkbox_width=16, checkbox_height=16,
                                  command=self.update_preview)
            box.select()
            box.grid(row=0, column=column, padx=(10, 0))
            widgets["seq_need_sep"] = box
            self.update_preview()
            self.fit_window()

        def handle_mode_change(*args):
            for child in options.winfo_children():
                child.destroy()
            for key in widgets:
                widgets[key] = None
            mode = new_var.get()
            if mode == "String Substitution":
                entry("find", "Find", column=0)
                entry("replace", "Replace", column=1)
            elif mode in ("Text", "Prefix", "Postfix"):
                entry("text_entry", {"Text": "Enter Text", "Prefix": "Enter Prefix",
                                     "Postfix": "Enter Postfix"}[mode])
            elif mode == "Date":
                def date_changed(choice):
                    if choice == "Custom" and not widgets["custom_date"]:
                        entry("custom_date", "%Y%m%d", 110, 1, "%Y%m%d")
                    elif choice != "Custom" and widgets["custom_date"]:
                        widgets["custom_date"].destroy()
                        widgets["custom_date"] = None
                    self.update_preview()
                menu("date_dropdown", list(DATE_FORMATS) + ["Custom"], "YYYYMMDD", 140, 0, date_changed)
            elif mode == "Number Counter":
                entry("counter_start", "Start Value", 100, 0)
                entry("counter_increment", "Increment", 100, 1)
            elif mode == "Sequence Number":
                menu("seq_action", SEQ_ACTIONS, "Remove", 110, 0, show_sequence_options)
                show_sequence_options("Remove")
            self.update_preview()
            self.fit_window()

        new_var.trace_add("write", handle_mode_change)
        self.text_option_widgets.append(item)
        handle_mode_change()

    def remove_rule(self, item):
        if item in self.text_option_widgets:
            index = self.text_option_widgets.index(item)
            self.text_option_widgets.pop(index)
            self.text_option_vars.pop(index)
            item["frame"].destroy()
            self.update_preview()
            self.fit_window()

    def remove_text_option(self):
        """Remove the last rule."""
        if self.text_option_widgets:
            self.remove_rule(self.text_option_widgets[-1])

    def rules(self):
        """The rule rows as plain dicts for the naming engine."""
        def get(widget, default=""):
            return widget.get() if widget is not None else default

        result = []
        for item in self.text_option_widgets:
            w = item["widgets"]
            mode = item["var"].get()
            rule = {"mode": mode}
            if mode == "String Substitution":
                rule.update(find=get(w["find"]), replace=get(w["replace"]))
            elif mode in ("Text", "Prefix", "Postfix"):
                rule.update(text=get(w["text_entry"]))
            elif mode == "Date":
                rule.update(date_format=get(w["date_dropdown"], "YYYY-MM-DD"), custom=get(w["custom_date"]))
            elif mode == "Number Counter":
                rule.update(start=get(w["counter_start"]), increment=get(w["counter_increment"]))
            elif mode == "Sequence Number":
                rule.update(action=get(w["seq_action"], "Remove"), start=get(w["seq_start"], "1"),
                            step=get(w["seq_step"], "1"), offset=get(w["seq_offset"], "0"),
                            digits=get(w["seq_digits"], "Auto"), order=get(w["seq_order"], SEQ_ORDERS[0]),
                            need_separator=bool(get(w["seq_need_sep"], 1)))
            else:
                continue
            result.append(rule)
        return result

    def builder_settings(self):
        return {"join": self.join_var.get(), "counter_start": self.counter_start_var.get(),
                "counter_step": self.counter_step_var.get(), "counter_digits": self.counter_digits_var.get(),
                "date_format": self.date_format_var.get()}

    def compute(self):
        """New names and a status for every file in the list."""
        files = list(self.selected_files)
        affect = bool(self.affect_extension_checkbox.get())
        names, numbers, notes = apply_rules(files, self.rules(), affect)
        names = build_names(files, names, numbers, self.layout, self.builder_settings())
        return files, names, check_names(files, names, notes)

    # ----------------------------------------------------------------- preview
    def update_preview(self, *args):
        self.refresh_table()

    def refresh_table(self):
        if not getattr(self, "_ready", False) or not self.tree.winfo_exists():
            return
        files, names, statuses = self.compute()
        self.rows = list(zip(files, names, statuses))
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for i, (path, new, (status, level)) in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(i), values=(os.path.basename(path), new, status), tags=(level,))
        keep = [iid for iid in selected if self.tree.exists(iid)]
        if keep:
            self.tree.selection_set(keep)
        self._retint_table()

        changing = sum(1 for _, _, (_, level) in self.rows if level in ("ok", "note"))
        conflicts = sum(1 for _, _, (_, level) in self.rows if level == "conflict")
        summary = f"{len(files)} files · {changing} will change"
        if conflicts:
            summary += f" · {conflicts} problem{'s' if conflicts != 1 else ''}"
        self.summary_label.configure(text=summary if files else "",
                                     text_color=C["danger"] if conflicts else C["text_muted"])
        self.file_count_label.configure(text=f"{len(files)} files will be processed")

        focus = keep[0] if keep else ("0" if self.rows else None)
        if focus is not None:
            path, new, _ = self.rows[int(focus)]
            self.current_preview.configure(text=f"Current Filename: {os.path.basename(path)}")
            self.new_preview.configure(text=f"New Filename: {new}")
        else:
            self.current_preview.configure(text="Current Filename: ")
            self.new_preview.configure(text="New Filename: ")

    def _retint_table(self, _mode=None):
        if not self.tree.winfo_exists():
            return
        i = theme.mode_index()
        self.tree.tag_configure("conflict", foreground=C["danger"][i])
        self.tree.tag_configure("same", foreground=C["text_subtle"][i])
        self.tree.tag_configure("note", foreground=C["warning"][i])
        self.tree.tag_configure("ok", foreground=C["text"][i])

    # ----------------------------------------------------------------- files
    def add_paths(self, paths):
        known = {os.path.normcase(p) for p in self.selected_files}
        new = [p for p in paths if os.path.isfile(p) and os.path.normcase(p) not in known]
        new.sort(key=lambda p: natural_key(os.path.basename(p)))
        self.selected_files.extend(new)
        self.refresh_table()

    def open_files(self):
        file_paths = filedialog.askopenfilenames(title="Select Files", filetypes=[("All Files", "*.*")],
                                                 parent=getattr(self, "window", None))
        if file_paths:
            self.add_paths(list(file_paths))

    def open_folder(self):
        folder = filedialog.askdirectory(title="Add every file in a folder", parent=self.window)
        if folder:
            try:
                names = os.listdir(folder)
            except OSError as exc:
                messagebox.showerror("Batch Renamer", error_help.explain(exc, folder, "read the folder"),
                                     parent=self.window)
                return
            self.add_paths([os.path.join(folder, n) for n in names
                            if not n.startswith(".") and not n.startswith("._")])

    def remove_selected(self):
        drop = {int(iid) for iid in self.tree.selection()}
        if drop:
            self.selected_files = [p for i, p in enumerate(self.selected_files) if i not in drop]
            self.tree.selection_set(())
            self.refresh_table()

    def clear_all(self):
        self.selected_files = []
        for item in self.text_option_widgets:
            for widget in item["widgets"].values():
                if isinstance(widget, ctk.CTkEntry):
                    widget.delete(0, tk.END)
        self.refresh_table()

    # ----------------------------------------------------------------- rename
    def rename_files(self):
        if not self.selected_files:
            messagebox.showerror("Batch Renamer", "Add some files first.", parent=self.window)
            return
        files, names, statuses = self.compute()
        problems = [(os.path.basename(p), n, s) for p, n, (s, level) in zip(files, names, statuses)
                    if level == "conflict"]
        if problems:
            listed = "\n".join(f"  • {old}  →  {new or '(empty)'}\n      {why}" for old, new, why in problems[:12])
            more = f"\n  …and {len(problems) - 12} more" if len(problems) > 12 else ""
            messagebox.showerror(
                "Batch Renamer",
                f"Nothing was renamed. {len(problems)} file(s) would get a name that can't be used:\n\n"
                f"{listed}{more}\n\nThey're shown in red in the list. Change the rules or remove those files.",
                parent=self.window)
            return
        pairs = [(path, os.path.join(os.path.dirname(path), new)) for path, new in zip(files, names)]
        try:
            done = rename_all(pairs)
        except OSError as exc:
            messagebox.showerror("Batch Renamer",
                                 error_help.explain(exc, action="rename the files",
                                                    extra="Nothing was renamed; any files already done were "
                                                          "put back."),
                                 parent=self.window)
            return
        self.undo_pairs = done
        self.undo_button.configure(state="normal" if done else "disabled")
        self.selected_files = [new for _, new in pairs]
        self.refresh_table()
        self.file_count_label.configure(text=f"Renamed {len(done)} file{'s' if len(done) != 1 else ''}.")

    def undo_rename(self):
        if not self.undo_pairs:
            return
        try:
            rename_all([(new, old) for old, new in self.undo_pairs])
        except OSError as exc:
            messagebox.showerror("Batch Renamer", error_help.explain(exc, action="undo the rename"),
                                 parent=self.window)
            return
        back = {os.path.normcase(new): old for old, new in self.undo_pairs}
        self.selected_files = [back.get(os.path.normcase(p), p) for p in self.selected_files]
        count = len(self.undo_pairs)
        self.undo_pairs = []
        self.undo_button.configure(state="disabled")
        self.refresh_table()
        self.file_count_label.configure(text=f"Put back {count} original name{'s' if count != 1 else ''}.")

    # ----------------------------------------------------------------- name builder
    def make_chip(self, master, token, removable, on_remove=None, palette=False):
        builtin = token["kind"] != "text"
        chip = ctk.CTkFrame(master, corner_radius=12, border_width=1,
                            fg_color=C["accent_soft"] if builtin else C["control"],
                            border_color=C["accent_soft"] if builtin else C["border_strong"])
        text = ctk.CTkLabel(chip, text=chip_label(token), height=24,
                            font=theme.font("body_strong") if builtin else theme.font("mono"),
                            text_color=C["accent_text"] if builtin else C["text"])
        text.pack(side="left", padx=(10, 2 if removable else 10), pady=2)
        if removable:
            close = ctk.CTkLabel(chip, text="×", width=18, height=24, font=theme.sized_font(15),
                                 text_color=C["text_muted"], cursor="hand2")
            close.pack(side="left", padx=(0, 6))
            close.bind("<Button-1>", lambda e: on_remove())
        for widget in (chip, text):
            widget.bind("<ButtonPress-1>", lambda e, t=token, c=chip, p=palette: self.drag_start(e, t, c, p), add="+")
            widget.bind("<B1-Motion>", self.drag_motion, add="+")
            widget.bind("<ButtonRelease-1>", self.drag_end, add="+")
            try:
                tk.Misc.configure(widget, cursor="fleur" if not palette else "hand2")
            except tk.TclError:
                pass
        if token["kind"] == "text" and not palette:
            text.bind("<Double-Button-1>", lambda e, t=token: self.edit_token(t), add="+")
        if builtin:
            self.tooltip(chip, BUILTIN_TOKENS[token["kind"]][1])
            self.tooltip(text, BUILTIN_TOKENS[token["kind"]][1])
        return chip

    def tooltip(self, widget, text):
        widget.bind("<Enter>", lambda e: self.summary_hint(text), add="+")
        widget.bind("<Leave>", lambda e: self.summary_hint(None), add="+")

    def summary_hint(self, text):
        if hasattr(self, "hint_label"):
            self.hint_label.configure(text=text or "")

    def render_strip(self):
        chips = []
        for index, token in enumerate(self.layout):
            chips.append(self.make_chip(self.strip, token, True,
                                        on_remove=lambda i=index: self.remove_token(i)))
        self.strip.set_chips(chips)
        self.show_token_settings()
        self.update_preview()

    def show_token_settings(self):
        kinds = {token["kind"] for token in self.layout}
        shown = False
        for frame, kind in ((self.counter_settings, "counter"), (self.date_settings, "date")):
            if kind in kinds:
                frame.pack(side="left", padx=(0, 24))
                shown = True
            else:
                frame.pack_forget()
        if shown:
            self.token_settings.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        else:
            self.token_settings.grid_remove()
        self.fit_window()

    def render_palette(self):
        chips = [self.make_chip(self.palette, {"kind": kind}, False, palette=True) for kind in BUILTIN_TOKENS]
        for text in self.saved_tokens:
            chips.append(self.make_chip(self.palette, {"kind": "text", "text": text}, True,
                                        on_remove=lambda t=text: self.forget_token(t), palette=True))
        self.palette.set_chips(chips)

    def reset_layout(self):
        self.layout = [{"kind": "name"}]
        self.render_strip()

    def remove_token(self, index):
        if 0 <= index < len(self.layout):
            self.layout.pop(index)
            self.render_strip()

    def insert_token(self, token, index=None):
        token = dict(token)
        self.layout.insert(len(self.layout) if index is None else index, token)
        self.render_strip()

    def add_custom_token(self):
        text = self.custom_entry.get()
        if not text:
            return
        if os.name == "nt" and error_help.name_problems(text.strip(" .") or "x"):
            bad = "".join(sorted({c for c in text if c in error_help.WINDOWS_BAD_CHARS}))
            messagebox.showerror("Batch Renamer", f"“{text}” can't go in a file name: Windows doesn't allow "
                                                  f"{' '.join(bad) or 'that'} in names.", parent=self.window)
            return
        if "/" in text or "\0" in text:
            messagebox.showerror("Batch Renamer", "A token can't contain “/”.", parent=self.window)
            return
        self.custom_entry.delete(0, tk.END)
        if text not in self.saved_tokens:
            self.saved_tokens.append(text)
            self.save_saved_tokens()
            self.render_palette()
        self.insert_token({"kind": "text", "text": text})

    def forget_token(self, text):
        if text in self.saved_tokens:
            self.saved_tokens.remove(text)
            self.save_saved_tokens()
            self.render_palette()

    def edit_token(self, token):
        dialog = ctk.CTkInputDialog(title="Edit token", text="Text for this token:")
        value = dialog.get_input()
        if value:
            token["text"] = value
            self.render_strip()

    def load_saved_tokens(self):
        try:
            with open(TOKENS_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
            return [t for t in data.get("tokens", []) if isinstance(t, str) and t]
        except (OSError, ValueError, AttributeError):
            return ["_GFX_", "_VFX_", "_TEMP_", "_FINAL"]

    def save_saved_tokens(self):
        try:
            with open(TOKENS_FILE, "w", encoding="utf-8") as handle:
                json.dump({"tokens": self.saved_tokens}, handle, indent=2)
        except OSError:
            pass            # not fatal: the token still works this session

    # drag and drop ---------------------------------------------------------
    def drag_start(self, event, token, chip, from_palette):
        self.drag = {"token": token, "chip": chip, "palette": from_palette, "x": event.x_root,
                     "y": event.y_root, "moving": False, "ghost": None,
                     "index": None if from_palette else self._chip_index(chip)}

    def _chip_index(self, chip):
        return self.strip.chips.index(chip) if chip in self.strip.chips else None

    def drag_motion(self, event):
        drag = self.drag
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
            i = theme.mode_index()
            tk.Label(ghost, text=chip_label(drag["token"]), bg=C["accent"][i], fg="#FFFFFF", padx=10, pady=3,
                     font=(theme.font("body_strong").cget("family"), 11, "bold")).pack()
            drag["ghost"] = ghost
            if not drag["palette"]:
                drag["chip"].configure(fg_color=C["hover"], border_color=C["accent"])
        drag["ghost"].geometry(f"+{event.x_root + 12}+{event.y_root + 10}")
        if self.strip.contains(event.x_root, event.y_root):
            index = self.strip.index_at(event.x_root, event.y_root)
            x, y, h = self.strip.marker_position(index)
            scale = ctk.ScalingTracker.get_widget_scaling(self.strip)
            self.marker.place(x=x / scale, y=y / scale)
            self.marker.configure(height=max(h, 20) / scale)
            self.marker.lift()
        else:
            self.marker.place_forget()

    def drag_end(self, event):
        drag, self.drag = self.drag, None
        self.marker.place_forget()
        if not drag:
            return
        if drag["ghost"] is not None:
            drag["ghost"].destroy()
        if not drag["moving"]:
            if drag["palette"]:                           # a click on a palette token adds it at the end
                self.insert_token(drag["token"])
            return
        if not self.strip.contains(event.x_root, event.y_root):
            self.render_strip()                           # dropped elsewhere: put it back
            return
        index = self.strip.index_at(event.x_root, event.y_root)
        if drag["palette"]:
            self.insert_token(drag["token"], index)
            return
        old = drag["index"]
        if old is None:
            return
        token = self.layout.pop(old)
        if index > old:
            index -= 1
        self.layout.insert(index, token)
        self.render_strip()
