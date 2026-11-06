import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

import theme
from base_tool import BaseTool

TEXT_TYPES = [
    ("Text-based files", "*.txt *.edl *.md *.csv *.tsv *.ale *.srt *.vtt *.xml *.json *.log *.py *.cdl *.fcpxml"),
    ("Word documents", "*.docx"),
    ("EDL files", "*.edl"),
    ("All files", "*.*"),
]


def read_text_file(path):
    """Read a text file, guessing the encoding. Returns (text, encoding)."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        import chardet
        guess = chardet.detect(raw).get("encoding")
        if guess:
            return raw.decode(guess), guess
    except Exception:
        pass
    return raw.decode("latin-1"), "latin-1"


def read_docx(path):
    import docx
    document = docx.Document(path)
    return "\n".join(p.text for p in document.paragraphs)


def write_docx(path, text):
    """Write text into a .docx, reusing the original's paragraph styles when it exists."""
    import docx
    lines = text.split("\n")
    document = docx.Document(path) if os.path.exists(path) else docx.Document()
    paragraphs = document.paragraphs
    for i, line in enumerate(lines):
        if i < len(paragraphs):
            para = paragraphs[i]
            if para.text != line:
                for run in para.runs[1:]:
                    run._element.getparent().remove(run._element)
                if para.runs:
                    para.runs[0].text = line
                else:
                    para.add_run(line)
        else:
            document.add_paragraph(line)
    for para in paragraphs[len(lines):]:
        para._element.getparent().remove(para._element)
    document.save(path)


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Text Editor"
        self.description = "Open and edit text files, EDLs and Word documents"
        self.category = "Editorial & Turnover"
        self.icon = "pencil-line"

    def create_window(self, parent):
        window = self.create_tool_window(parent, self.name, 960, 720)
        window.minsize(640, 420)
        self.window = window
        self.path = None
        self.encoding = "utf-8"
        self.line_ending = "\n"
        self.is_docx = False
        self.wrap_var = tk.BooleanVar(value=True)
        self._last_find = ""

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 12))

        actions = theme.action_bar(page, pady=(0, 10))
        theme.Button(actions, "New", kind="ghost", width=64, command=self.new_file).pack(side="left")
        theme.Button(actions, "Open", kind="ghost", width=64, command=self.open_file).pack(side="left", padx=(6, 0))
        theme.Button(actions, "Save As", kind="ghost", width=80, command=self.save_as).pack(side="right")
        theme.Button(actions, "Save", kind="primary", width=80, command=self.save).pack(side="right", padx=(0, 6))
        ctk.CTkSwitch(actions, text="Wrap", variable=self.wrap_var, command=self.apply_wrap,
                      width=60).pack(side="right", padx=(0, 14))

        # Find / replace bar
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        self.find_entry = ctk.CTkEntry(bar, placeholder_text="Find", width=200)
        self.find_entry.pack(side="left")
        self.replace_entry = ctk.CTkEntry(bar, placeholder_text="Replace with", width=200)
        self.replace_entry.pack(side="left", padx=(6, 0))
        self.case_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="Match case", variable=self.case_var, width=20).pack(side="left", padx=10)
        theme.Button(bar, "Find Next", kind="ghost", width=84, command=self.find_next).pack(side="left")
        theme.Button(bar, "Replace", kind="ghost", width=72, command=self.replace_one).pack(side="left", padx=(6, 0))
        theme.Button(bar, "Replace All", kind="ghost", width=90, command=self.replace_all).pack(side="left", padx=(6, 0))

        self.textbox = ctk.CTkTextbox(page, undo=True, wrap="word", font=theme.sized_font(13, mono=True))
        self.textbox.pack(fill="both", expand=True)
        self.text = self.textbox._textbox
        self.text.configure(autoseparators=True, maxundo=-1)
        self.text.tag_config("found", background="#f5c542", foreground="#000000")

        self.status = ctk.CTkLabel(page, text="", anchor="w")
        self.status.pack(fill="x", pady=(6, 0))

        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<KeyRelease>", lambda e: self.update_status())
        self.text.bind("<ButtonRelease-1>", lambda e: self.update_status())
        self.find_entry.bind("<Return>", lambda e: self.find_next())
        # "Command" is treated as no modifier on Windows, so plain letters would fire the shortcuts
        for mod in (("Command",) if sys.platform == "darwin" else ("Control",)):
            window.bind(f"<{mod}-s>", lambda e: self.save())
            window.bind(f"<{mod}-o>", lambda e: self.open_file())
            window.bind(f"<{mod}-n>", lambda e: self.new_file())
            window.bind(f"<{mod}-f>", lambda e: (self.find_entry.focus_set(), "break")[1])
            window.bind(f"<{mod}-g>", lambda e: self.find_next())
        window.bind("<Control-y>", lambda e: self._redo())
        window.bind("<Control-Shift-Z>", lambda e: self._redo())
        window.protocol("WM_DELETE_WINDOW", self.close)

        self.update_title()
        self.update_status()
        return window

    # --- helpers ---------------------------------------------------------
    def _redo(self):
        try:
            self.text.edit_redo()
        except tk.TclError:
            pass
        return "break"

    def _on_modified(self, _event=None):
        self.update_title()

    @property
    def dirty(self):
        return bool(self.text.edit_modified())

    def update_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.window.title(f"{'â€¢ ' if self.dirty else ''}{name} - {self.name}")

    def update_status(self):
        line, col = self.text.index("insert").split(".")
        chars = len(self.text.get("1.0", "end-1c"))
        words = len(self.text.get("1.0", "end-1c").split())
        kind = "Word document" if self.is_docx else self.encoding
        self.status.configure(text=f"Line {line}, Col {int(col) + 1}   |   {words} words, {chars} characters   |   {kind}")

    def apply_wrap(self):
        self.text.configure(wrap="word" if self.wrap_var.get() else "none")

    def _set_content(self, content):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.text.mark_set("insert", "1.0")
        self.text.see("1.0")
        self.update_title()
        self.update_status()

    def confirm_discard(self):
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel("Unsaved changes", "Save changes before continuing?", parent=self.window)
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    # --- file actions ----------------------------------------------------
    def new_file(self):
        if not self.confirm_discard():
            return
        self.path, self.encoding, self.line_ending, self.is_docx = None, "utf-8", "\n", False
        self._set_content("")

    def open_file(self, path=None):
        if not self.confirm_discard():
            return
        path = path or filedialog.askopenfilename(title="Open file", filetypes=TEXT_TYPES, parent=self.window)
        if not path:
            return
        try:
            if path.lower().endswith(".docx"):
                content, encoding, is_docx = read_docx(path), "docx", True
                line_ending = "\n"
            else:
                content, encoding = read_text_file(path)
                is_docx = False
                line_ending = "\r\n" if "\r\n" in content else ("\r" if "\r" in content else "\n")
                content = content.replace("\r\n", "\n").replace("\r", "\n")
        except ImportError:
            messagebox.showerror("Missing package", "Word documents need python-docx (pip install python-docx).",
                                 parent=self.window)
            return
        except Exception as e:
            messagebox.showerror("Could not open file", str(e), parent=self.window)
            return
        self.path, self.encoding, self.line_ending, self.is_docx = path, encoding, line_ending, is_docx
        self._set_content(content)

    def save(self):
        if not self.path:
            return self.save_as()
        return self._write(self.path)

    def save_as(self):
        path = filedialog.asksaveasfilename(
            title="Save as", filetypes=TEXT_TYPES, defaultextension=".txt",
            initialfile=os.path.basename(self.path) if self.path else "", parent=self.window)
        if not path:
            return False
        return self._write(path)

    def _write(self, path):
        content = self.text.get("1.0", "end-1c")
        try:
            if path.lower().endswith(".docx"):
                write_docx(path, content)
                self.is_docx, self.encoding = True, "docx"
            else:
                if self.is_docx:
                    self.is_docx, self.encoding = False, "utf-8"
                data = content.replace("\n", self.line_ending)
                with open(path, "w", encoding=self.encoding, newline="") as f:
                    f.write(data)
        except ImportError:
            messagebox.showerror("Missing package", "Word documents need python-docx (pip install python-docx).",
                                 parent=self.window)
            return False
        except Exception as e:
            messagebox.showerror("Could not save file", str(e), parent=self.window)
            return False
        self.path = path
        self.text.edit_modified(False)
        self.update_title()
        self.update_status()
        return True

    def close(self):
        if self.confirm_discard():
            self.window.destroy()

    # --- find / replace --------------------------------------------------
    def _search(self, start, backwards=False):
        needle = self.find_entry.get()
        if not needle:
            return None
        return self.text.search(needle, start, stopindex="1.0" if backwards else "end",
                                nocase=not self.case_var.get(), backwards=backwards), needle

    def _select(self, index, length):
        end = f"{index}+{length}c"
        self.text.tag_remove("sel", "1.0", "end")
        self.text.tag_add("sel", index, end)
        self.text.mark_set("insert", end)
        self.text.see(index)
        self.update_status()

    def find_next(self):
        result = self._search("insert")
        if not result:
            return
        index, needle = result
        if not index:
            result = self._search("1.0")  # wrap around
            index = result[0]
        if index:
            self._select(index, len(needle))
        else:
            self.status.configure(text="Not found")

    def _selection_matches(self):
        needle = self.find_entry.get()
        try:
            selected = self.text.get("sel.first", "sel.last")
        except tk.TclError:
            return False
        if self.case_var.get():
            return selected == needle
        return selected.lower() == needle.lower()

    def replace_one(self):
        if not self._selection_matches():
            self.find_next()
            return
        self.text.delete("sel.first", "sel.last")
        self.text.insert("insert", self.replace_entry.get())
        self.find_next()

    def replace_all(self):
        needle = self.find_entry.get()
        if not needle:
            return
        replacement = self.replace_entry.get()
        count = 0
        index = "1.0"
        self.text.edit_separator()
        while True:
            index = self.text.search(needle, index, stopindex="end", nocase=not self.case_var.get())
            if not index:
                break
            self.text.delete(index, f"{index}+{len(needle)}c")
            self.text.insert(index, replacement)
            index = f"{index}+{len(replacement)}c"
            count += 1
        self.text.edit_separator()
        self.update_status()
        self.status.configure(text=f"Replaced {count} occurrence{'s' if count != 1 else ''}")
