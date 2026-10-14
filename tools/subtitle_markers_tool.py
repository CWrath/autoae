import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog
import os
import re
import chardet
import subprocess
from base_tool import BaseTool
import theme
from theme import C

class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Subtitle to Markers"
        self.description = "Convert subtitle files to timeline markers"
        self.category = "Editorial & Turnover"
        self.icon = "captions"

    def create_window(self, parent):
        window = self.create_tool_window(parent, "Subtitle to Markers Converter", 800, 600)
        window.minsize(640, 460)

        page = theme.page(window)
        theme.tool_header(
            page, self,
            subtitle="Turn Avid SubCap .txt exports into marker files for timeline import."
        ).pack(fill="x", pady=(0, 18))

        # Actions (packed before the list so they never get squeezed off the bottom)
        actions = theme.action_bar(page)
        self.clear_button = theme.Button(actions, "Clear", kind="ghost", width=76,
                                         command=self.clear_files, state="disabled")
        self.clear_button.pack(side="left")
        self.status_label = theme.muted(actions, "", role="body")
        self.status_label.pack(side="left", padx=(12, 0))
        theme.Button(actions, "Convert", kind="primary", width=120,
                     command=self.convert_files).pack(side="right")
        theme.Button(actions, "Add Files…", kind="secondary", width=110,
                     command=self.select_files).pack(side="right", padx=(0, 8))

        # Files
        card = theme.Card(page, title="SubCap files",
                          subtitle="Markers are written next to each file as <name>_markers.txt")
        card.pack(fill="both", expand=True)
        self.files_listbox = theme.ThemedListbox(card.body, selectmode="multiple")
        self.files_listbox.pack(expand=True, fill="both")

        # Store window reference
        self.window = window

        return window

    def select_files(self):
        """Select subtitle files"""
        files = filedialog.askopenfilenames(
            title="Select Subtitle Files",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )

        for file in files:
            self.files_listbox.insert("end", file)

        if files:
            self.clear_button.configure(state="normal")
            self.status_label.configure(text=f"{self.files_listbox.size()} files selected")

    def convert_files(self):
        """Convert subtitle files to markers"""
        files = self.files_listbox.get(0, "end")

        if not files:
            self.status_label.configure(text="No files selected.")
            return

        self.status_label.configure(text="Processing...")
        self.window.update()

        # Regex patterns
        timecode_pattern = re.compile(r'\d{2}:\d{2}:\d{2}:\d{2}')
        custom_text_patterns = [
            re.compile(r'@ This file written with the Avid Caption plugin, version 1'),
            re.compile(r'<begin subtitles>'),
            re.compile(r'<end subtitles>')
        ]

        processed_count = 0
        error_count = 0

        for file_path in files:
            try:
                # Detect encoding
                with open(file_path, 'rb') as f:
                    raw_data = f.read()
                    encoding = chardet.detect(raw_data)['encoding'] or 'utf-8'

                # Read file
                with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
                    text = f.read()

                # Process subtitles
                subtitle_blocks = []
                current_block = ''

                for line in text.split('\n'):
                    line = line.strip()
                    if not line:
                        if current_block:
                            subtitle_blocks.append(current_block)
                            current_block = ''
                    else:
                        # Skip custom patterns
                        skip = any(pattern.match(line) for pattern in custom_text_patterns)
                        if not skip:
                            current_block += line + ' '

                if current_block:
                    subtitle_blocks.append(current_block)

                # Write output
                output_path = file_path.replace('.txt', '_markers.txt')
                with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
                    marker_count = 0

                    for block in subtitle_blocks:
                        # Extract timecodes
                        timecodes = timecode_pattern.findall(block)
                        if len(timecodes) >= 2:
                            start_tc = timecodes[0]

                            # Extract text
                            text = re.sub(timecode_pattern, '', block).strip()
                            text = self.remove_special_characters(text)

                            if text:
                                f.write(f"SUBTITLE\t{start_tc}\tTC1\tblue\t{text}\t1\n")
                                marker_count += 1

                    if marker_count > 0:
                        processed_count += 1

            except Exception as e:
                error_count += 1
                print(f"Error processing {file_path}: {str(e)}")

        # Update status
        if processed_count > 0:
            self.status_label.configure(
                text=f"Complete! Processed {processed_count} files successfully. {error_count} errors."
            )

            # Open output folder
            if files:
                folder = os.path.dirname(os.path.abspath(files[0]))
                if os.name == 'nt':
                    os.startfile(folder)
                elif os.name == 'posix':
                    subprocess.call(['open', folder])
        else:
            self.status_label.configure(text="No valid subtitle data found.")

    def remove_special_characters(self, text):
        """Remove special characters from text"""
        special_chars = ["'", """, """, "–", "…", "ï»¿", "'", '"']
        for char in special_chars:
            text = text.replace(char, "")
        return text

    def clear_files(self):
        """Clear file list"""
        self.files_listbox.delete(0, 'end')
        self.status_label.configure(text="")
        self.clear_button.configure(state="disabled")
