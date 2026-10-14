import csv
import os
import re
from tkinter import filedialog, messagebox, simpledialog, ttk

import customtkinter as ctk
from base_tool import BaseTool
import theme
from theme import C


class Tool(BaseTool):
    """Build, save, and deploy reusable folder structures."""

    TEMPLATE_FILES = {
        "Avid Media Composer - Film - Edit": "avid_film.csv",
        "Avid Media Composer - Series 8 Ep - Edit": "avid_series.csv",
        "Adobe Premiere Pro - Film - Edit": "pr_film.csv",
        "Adobe Premiere Pro - Series 8 Ep - Edit": "pr_series.csv",
        "Adobe Premiere Pro - Short/Commercial - Edit": "pr_short-commericial.csv",
    }
    DEFAULT_TEMPLATES = {
        "Avid Media Composer - Series 8 Ep - Edit": [
            "Avid MediaFiles", "Avid Projects", "Episode_01", "Episode_02",
            "Episode_03", "Episode_04", "Episode_05", "Episode_06",
            "Episode_07", "Episode_08", "Shared_Media", "Exports", "Documents",
        ],
        "Adobe Premiere Pro - Film - Edit": [
            "01_Project_Files", "02_Media/01_Dailies", "02_Media/02_Audio/01_Production",
            "02_Media/02_Audio/02_Music", "02_Media/02_Audio/03_SFX", "02_Media/03_VFX",
            "02_Media/04_Graphics", "03_Sequences/01_Assembly", "03_Sequences/02_Rough_Cut",
            "03_Sequences/03_Fine_Cut", "04_Exports", "05_Documents/01_Scripts",
            "05_Documents/02_Notes",
        ],
        "Adobe Premiere Pro - Series 8 Ep - Edit": [
            "01_Project_Files", "02_Media/Episode_01", "02_Media/Episode_02",
            "02_Media/Episode_03", "02_Media/Episode_04", "02_Media/Episode_05",
            "02_Media/Episode_06", "02_Media/Episode_07", "02_Media/Episode_08",
            "02_Media/Shared_Assets", "03_Sequences", "04_Exports", "05_Documents",
        ],
        "Adobe Premiere Pro - Short/Commercial - Edit": [
            "01_Project_Files", "02_Media/01_RAW", "02_Media/02_Audio",
            "02_Media/03_Graphics", "02_Media/04_Stock", "03_Sequences",
            "04_Exports", "05_Documents",
        ],
    }

    def __init__(self):
        super().__init__()
        self.name = "Folder Creator"
        self.description = "Create project folder structures from templates"
        self.category = "Project Setup"
        self.icon = "folder-tree"
        self.foldertemplates_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), os.pardir, "foldertemplates")
        )
        self.tree = None
        self.template_name = None
        self.template_selector = None

    def create_window(self, parent):
        window = self.create_tool_window(parent, "Folder Creator", 820, 680)
        window.minsize(680, 560)
        self.window = window
        theme.style_ttk(window)

        page = theme.page(window)
        theme.tool_header(
            page, self,
            subtitle="Build a reusable structure, save it as a template, then deploy it anywhere."
        ).pack(fill="x", pady=(0, 18))

        actions = theme.action_bar(page)
        theme.Button(
            actions, "Deploy to Destination…", kind="primary", width=190, height=38,
            command=self._deploy_template
        ).pack(side="right")

        # --- Template picker -------------------------------------------------
        template_card = theme.Card(page, title="Template")
        template_card.pack(fill="x")
        template_bar = template_card.body
        template_bar.grid_columnconfigure(0, weight=1)
        self.template_selector = ctk.CTkComboBox(
            template_bar,
            values=self._available_template_names(),
            command=self._load_selected_template,
            height=34,
        )
        self.template_selector.grid(row=0, column=0, sticky="ew")
        if self.template_selector.cget("values"):
            self.template_selector.set(self.template_selector.cget("values")[0])
        theme.Button(
            template_bar, "New…", kind="secondary", width=80, command=self._new_template
        ).grid(row=0, column=1, padx=(8, 0))
        theme.Button(
            template_bar, "Save…", kind="secondary", width=80, command=self._save_template
        ).grid(row=0, column=2, padx=(8, 0))

        # --- Folder tree ------------------------------------------------------
        folders_card = theme.Card(page, title="Folders",
                                  subtitle="Select a folder to add inside it, rename it or delete it.")
        folders_card.pack(fill="both", expand=True, pady=(14, 0))
        editor = folders_card.body
        editor.grid_columnconfigure(0, weight=1)
        editor.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(editor, show="tree", selectmode="browse")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(editor, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        tree_buttons = ctk.CTkFrame(editor, fg_color="transparent")
        tree_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        theme.Button(
            tree_buttons, "Add Folder…", kind="secondary", width=110,
            image=theme.icon("plus", C["text"], 15), compound="left", command=self._add_folder
        ).pack(side="left")
        theme.Button(tree_buttons, "Rename…", kind="ghost", width=90,
                     command=self._rename_folder).pack(side="left", padx=(8, 0))
        theme.Button(tree_buttons, "Delete", kind="danger", width=80,
                     command=self._delete_folder).pack(side="left", padx=(8, 0))

        self._load_selected_template(self.template_selector.get())
        return window

    def _available_template_names(self):
        os.makedirs(self.foldertemplates_dir, exist_ok=True)
        names = list(self.TEMPLATE_FILES)
        known_files = set(self.TEMPLATE_FILES.values())
        for filename in sorted(os.listdir(self.foldertemplates_dir)):
            if filename.lower().endswith(".csv") and filename not in known_files:
                names.append(os.path.splitext(filename)[0].replace("_", " ").title())
        return names

    def _template_path(self, name):
        filename = self.TEMPLATE_FILES.get(name)
        if filename is None:
            filename = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
            filename = (filename or "custom_template") + ".csv"
        return os.path.join(self.foldertemplates_dir, filename)

    def _load_selected_template(self, name):
        if not name or self.tree is None:
            return
        self.tree.delete(*self.tree.get_children())
        path = self._template_path(name)
        if not os.path.exists(path):
            for folder_path in self.DEFAULT_TEMPLATES.get(name, []):
                self._insert_path(folder_path)
            self.template_name = name
            return
        try:
            with open(path, newline="", encoding="utf-8") as template_file:
                for row in csv.reader(template_file):
                    if row and row[0].strip():
                        self._insert_path(row[0].strip())
            self.template_name = name
        except (OSError, csv.Error, UnicodeError) as error:
            messagebox.showerror("Template Error", f"Could not read the template:\n{error}")

    def _insert_path(self, folder_path):
        parent = ""
        for part in re.split(r"[/\\]+", folder_path.strip("/\\")):
            if not part:
                continue
            existing = next(
                (
                    item
                    for item in self.tree.get_children(parent)
                    if self.tree.item(item, "text") == part
                ),
                None,
            )
            parent = existing or self.tree.insert(parent, "end", text=part)

    def _add_folder(self):
        parent = self.tree.focus()
        name = simpledialog.askstring("Add Folder", "Folder name:", parent=self.window)
        if name is None:
            return
        name = name.strip()
        if not name or re.search(r"[/\\]", name) or name in (".", ".."):
            messagebox.showerror(
                "Invalid Folder Name",
                "Use a single folder name without path separators.",
            )
            return
        if any(self.tree.item(item, "text") == name for item in self.tree.get_children(parent)):
            messagebox.showerror("Duplicate Folder", "That folder already exists here.")
            return
        self.tree.insert(parent, "end", text=name)
        if parent:
            self.tree.item(parent, open=True)

    def _rename_folder(self):
        item = self.tree.focus()
        if not item:
            messagebox.showinfo("Rename Folder", "Select a folder to rename.")
            return
        current_name = self.tree.item(item, "text")
        name = simpledialog.askstring(
            "Rename Folder", "Folder name:", initialvalue=current_name, parent=self.window
        )
        if name is None:
            return
        name = name.strip()
        parent = self.tree.parent(item)
        if not name or re.search(r"[/\\]", name) or name in (".", ".."):
            messagebox.showerror("Invalid Folder Name", "Use a single folder name.")
            return
        if any(
            sibling != item and self.tree.item(sibling, "text") == name
            for sibling in self.tree.get_children(parent)
        ):
            messagebox.showerror("Duplicate Folder", "That folder already exists here.")
            return
        self.tree.item(item, text=name)

    def _delete_folder(self):
        item = self.tree.focus()
        if not item:
            messagebox.showinfo("Delete Folder", "Select a folder to delete.")
            return
        if messagebox.askyesno(
            "Delete Folder",
            "Delete this folder and all of its subfolders from the template?",
            parent=self.window,
        ):
            self.tree.delete(item)

    def _tree_paths(self, parent="", prefix=""):
        paths = []
        for item in self.tree.get_children(parent):
            path = os.path.join(prefix, self.tree.item(item, "text")) if prefix else self.tree.item(item, "text")
            paths.append(path)
            paths.extend(self._tree_paths(item, path))
        return paths

    def _save_template(self):
        name = simpledialog.askstring(
            "Save Template",
            "Template name:",
            initialvalue=self.template_name or self.template_selector.get(),
            parent=self.window,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("Save Template", "Enter a template name.")
            return
        paths = self._tree_paths()
        if not paths:
            messagebox.showerror("Save Template", "Add at least one folder first.")
            return
        path = self._template_path(name)
        try:
            os.makedirs(self.foldertemplates_dir, exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as template_file:
                writer = csv.writer(template_file)
                writer.writerows([[folder] for folder in paths])
            self.template_name = name
            values = self._available_template_names()
            self.template_selector.configure(values=values)
            self.template_selector.set(name)
            messagebox.showinfo("Template Saved", f"Saved {len(paths)} folders to:\n{path}")
        except OSError as error:
            messagebox.showerror("Save Template", f"Could not save the template:\n{error}")

    def _new_template(self):
        name = simpledialog.askstring("New Template", "Template name:", parent=self.window)
        if name is None:
            return
        name = name.strip()
        if not name:
            return
        self.tree.delete(*self.tree.get_children())
        self.template_name = name
        self.template_selector.configure(values=self._available_template_names() + [name])
        self.template_selector.set(name)

    def _deploy_template(self):
        paths = self._tree_paths()
        if not paths:
            messagebox.showerror("Deploy Template", "Add at least one folder first.")
            return
        destination = filedialog.askdirectory(
            title="Select destination for the folder structure"
        )
        if not destination:
            return
        created = 0
        try:
            for relative_path in paths:
                os.makedirs(os.path.join(destination, relative_path), exist_ok=True)
                created += 1
        except OSError as error:
            messagebox.showerror("Deploy Template", f"Deployment stopped:\n{error}")
            return
        messagebox.showinfo(
            "Deployment Complete",
            f"Created {created} folders in:\n{destination}",
        )
