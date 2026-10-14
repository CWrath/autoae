import customtkinter as ctk
from tkinter import filedialog
from base_tool import BaseTool
import theme
from theme import C

# Copy this file into tools/ to add a tool. It shows up on the launcher automatically.

class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Tool Name"  # This appears in the menu
        self.description = "Tool description"  # Shown on the launcher card and in the window header
        self.category = "Media"  # Project Setup | Media | Editorial & Turnover | Storage & Backup
        self.icon = "wrench"  # Any PNG name in assets/icons (Lucide icons), e.g. "film"

    def create_window(self, parent):
        # Create the window
        window = self.create_tool_window(parent, self.name, 800, 600)

        # Standard page: header first, then the action bar (so it stays visible), then cards
        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))

        actions = theme.action_bar(page)
        theme.Button(actions, "Clear", kind="ghost", width=76, command=self.clear).pack(side="left")
        theme.Button(actions, "Run", kind="primary", width=120, command=self.run).pack(side="right")

        # Main content frame
        card = theme.Card(page, title="Source")
        card.pack(fill="x")
        field = theme.PathField(card.body, "Folder", "Choose a folder", command=self.browse)
        field.pack(fill="x")
        self.source_entry = field.entry

        log_card = theme.Card(page, title="Log")
        log_card.pack(fill="both", expand=True, pady=(14, 0))
        self.log = theme.LogBox(log_card.body)
        self.log.pack(fill="both", expand=True)

        # Add your tool's functionality here
        self.window = window
        return window

    def browse(self):
        folder = filedialog.askdirectory(title="Choose a folder")
        if folder:
            self.source_entry.delete(0, "end")
            self.source_entry.insert(0, folder)

    def run(self):
        self.log.insert("end", f"Found: {self.source_entry.get() or 'no folder selected'}")

    def clear(self):
        self.source_entry.delete(0, "end")
        self.log.delete(0, "end")
