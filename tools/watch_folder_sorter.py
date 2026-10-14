import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import os
import shutil
from pathlib import Path
from datetime import datetime
import threading
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from base_tool import BaseTool
import theme

class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Watch Folder Sorter"
        self.description = "Automatically sort and rename media files from a watch folder"
        self.category = "Media"
        self.icon = "folder-input"
        self.observer = None
        self.is_watching = False
        
    def create_window(self, parent):
        window = self.create_tool_window(parent, "Watch Folder Sorter", 980, 760)
        window.minsize(860, 600)

        page = theme.page(window)
        theme.tool_header(
            page, self,
            subtitle="Sorts and renames new files as [CLIENT]_[TAG]_[YYMMDD]_OriginalName"
        ).pack(fill="x", pady=(0, 18))

        # Control buttons
        control_frame = theme.action_bar(page)
        self.process_existing_btn = theme.Button(control_frame, "Process Existing Files", kind="secondary",
                                                 command=self.process_existing_files, width=180)
        self.process_existing_btn.pack(side="left")
        self.start_button = theme.Button(control_frame, "Start Watching", kind="primary",
                                         command=self.toggle_watching, width=150)
        self.start_button.pack(side="right")

        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x")
        top.grid_columnconfigure(0, weight=3, uniform="top")
        top.grid_columnconfigure(1, weight=2, uniform="top")

        # --- Settings ---------------------------------------------------------
        settings_card = theme.Card(top, title="Folders")
        settings_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        settings_frame = settings_card.body
        settings_frame.grid_columnconfigure(0, weight=1)

        client_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        client_frame.grid(row=0, column=0, sticky="ew")
        theme.muted(client_frame, "Client code", role="body", width=120).pack(side="left", padx=(0, 12))
        self.client_code_entry = ctk.CTkEntry(client_frame, placeholder_text="ABC", width=90, height=34,
                                              font=theme.font("body_strong"))
        self.client_code_entry.pack(side="left")
        self.client_code_entry.bind("<KeyRelease>", self.validate_client_code)
        theme.muted(client_frame, "3 letters").pack(side="left", padx=(10, 0))

        watch_field = theme.PathField(settings_frame, "Watch folder", "Folder to watch",
                                      command=self.browse_watch_folder)
        watch_field.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.watch_folder_entry = watch_field.entry

        output_field = theme.PathField(settings_frame, "Output folder", "Where sorted files go",
                                       command=self.browse_output_folder)
        output_field.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.output_folder_entry = output_field.entry

        # --- Tag mapping --------------------------------------------------------
        tag_card = theme.Card(top, title="File type tags")
        tag_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        tag_frame = tag_card.body
        tag_frame.grid_columnconfigure(0, weight=1)

        # Create tag mappings
        self.tag_mappings = {
            "Video Files": {
                "extensions": [".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg"],
                "var": tk.StringVar(value="FTG"),
                "label": "Video Files (.mp4, .mov, .avi, etc.)"
            },
            "Audio Files": {
                "extensions": [".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".aiff", ".wma"],
                "var": tk.StringVar(value="SFX"),
                "label": "Audio Files (.mp3, .wav, .aac, etc.)"
            },
            "Image Files": {
                "extensions": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".svg", ".webp"],
                "var": tk.StringVar(value="GFX"),
                "label": "Image Files (.jpg, .png, .gif, etc.)"
            },
            "Music Files": {
                "extensions": [".mid", ".midi"],
                "var": tk.StringVar(value="MUSIC"),
                "label": "Music Files (specific)"
            },
            "Voice Over": {
                "extensions": [],
                "var": tk.StringVar(value="VO"),
                "label": "Voice Over Files"
            },
            "Project Files": {
                "extensions": [".prproj", ".aep", ".drp", ".fcp", ".fcpx", ".edl", ".xml", ".omf", ".aaf"],
                "var": tk.StringVar(value="PROJ"),
                "label": "Project Files (.prproj, .aep, etc.)"
            }
        }

        # Tag options
        tag_options = ["FTG", "GFX", "SFX", "MUSIC", "VO", "PROJ", "MISC"]

        # Create tag mapping UI: name on top, the extensions it covers underneath
        for row, (category, info) in enumerate(self.tag_mappings.items()):
            name, _, detail = info["label"].partition(" (")
            text = ctk.CTkFrame(tag_frame, fg_color="transparent")
            text.grid(row=row, column=0, sticky="w", pady=(0 if row == 0 else 6, 0))
            ctk.CTkLabel(text, text=name, anchor="w", height=18).pack(anchor="w")
            if detail:
                theme.muted(text, detail.rstrip(")"), height=14).pack(anchor="w")

            menu = ctk.CTkOptionMenu(tag_frame, variable=info["var"],
                                     values=tag_options, width=100, height=30)
            menu.grid(row=row, column=1, sticky="e", pady=(0 if row == 0 else 6, 0))

        # --- Status and log ---------------------------------------------------
        status_card = theme.Card(page, title="Activity log")
        status_card.pack(fill="both", expand=True, pady=(14, 0))
        self.status_label = theme.StatusLabel(status_card.header, text="Status: Not watching",
                                              rules=[("Watching", "success")], height=20)
        self.status_label.pack(side="right")

        self.log_text = theme.LogBox(status_card.body, empty_text="Sorted files will be listed here.",
                                     wrap="word", height=160)
        self.log_text.pack(fill="both", expand=True)

        # Store window reference
        self.window = window

        return window

    def validate_client_code(self, event):
        """Validate and format client code to 3 uppercase letters"""
        value = self.client_code_entry.get().upper()
        # Remove non-letter characters
        value = ''.join(c for c in value if c.isalpha())
        # Limit to 3 characters
        value = value[:3]
        
        # Update entry
        self.client_code_entry.delete(0, tk.END)
        self.client_code_entry.insert(0, value)
    
    def browse_watch_folder(self):
        """Browse for watch folder"""
        folder = filedialog.askdirectory(title="Select Watch Folder")
        if folder:
            self.watch_folder_entry.delete(0, tk.END)
            self.watch_folder_entry.insert(0, folder)
    
    def browse_output_folder(self):
        """Browse for output folder"""
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.output_folder_entry.delete(0, tk.END)
            self.output_folder_entry.insert(0, folder)
    
    def log_message(self, message):
        """Add message to log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.window.update()
    
    def get_file_tag(self, file_path):
        """Determine the appropriate tag for a file based on its extension"""
        ext = Path(file_path).suffix.lower()
        
        # Check each category for matching extension
        for category, info in self.tag_mappings.items():
            if ext in info["extensions"]:
                return info["var"].get()
        
        # Check for voice over files (might need custom logic)
        filename = Path(file_path).stem.lower()
        if any(vo_keyword in filename for vo_keyword in ["voice", "vo", "narration", "dialog"]):
            return self.tag_mappings["Voice Over"]["var"].get()
        
        # Default to MISC for unknown types
        return "MISC"
    
    def process_file(self, file_path):
        """Process and move a single file"""
        try:
            # Get client code
            client_code = self.client_code_entry.get().upper()
            if len(client_code) != 3:
                self.log_message(f"Error: Client code must be 3 letters")
                return
            
            # Get file info
            file_path = Path(file_path)
            if not file_path.exists() or file_path.is_dir():
                return
            
            # Get tag based on file type
            tag = self.get_file_tag(file_path)
            
            # Get current date
            date_str = datetime.now().strftime("%y%m%d")
            
            # Create new filename
            original_name = file_path.name
            new_name = f"{client_code}_{tag}_{date_str}_{original_name}"
            
            # Create output directory structure
            output_base = Path(self.output_folder_entry.get())
            date_folder = output_base / date_str
            tag_folder = date_folder / tag
            tag_folder.mkdir(parents=True, exist_ok=True)
            
            # Move and rename file
            destination = tag_folder / new_name
            
            # Handle existing files
            if destination.exists():
                # Add number to filename if it already exists
                counter = 1
                while destination.exists():
                    name_parts = new_name.rsplit('.', 1)
                    if len(name_parts) == 2:
                        new_dest_name = f"{name_parts[0]}_{counter}.{name_parts[1]}"
                    else:
                        new_dest_name = f"{new_name}_{counter}"
                    destination = tag_folder / new_dest_name
                    counter += 1
            
            # Move the file
            shutil.move(str(file_path), str(destination))
            
            self.log_message(f"Processed: {original_name} -> {tag}/{destination.name}")
            
        except Exception as e:
            self.log_message(f"Error processing {file_path}: {str(e)}")
    
    def toggle_watching(self):
        """Start or stop watching the folder"""
        if not self.is_watching:
            # Validate inputs
            if not self.client_code_entry.get() or len(self.client_code_entry.get()) != 3:
                messagebox.showerror("Error", "Please enter a 3-letter client code")
                return
            
            if not self.watch_folder_entry.get() or not os.path.exists(self.watch_folder_entry.get()):
                messagebox.showerror("Error", "Please select a valid watch folder")
                return
            
            if not self.output_folder_entry.get() or not os.path.exists(self.output_folder_entry.get()):
                messagebox.showerror("Error", "Please select a valid output folder")
                return
            
            # Start watching
            self.start_watching()
        else:
            # Stop watching
            self.stop_watching()
    
    def start_watching(self):
        """Start watching the folder"""
        try:
            self.event_handler = FileHandler(self)
            self.observer = Observer()
            self.observer.schedule(self.event_handler, self.watch_folder_entry.get(), recursive=False)
            self.observer.start()
            
            self.is_watching = True
            self.start_button.configure(text="Stop Watching")
            self.status_label.configure(text="Status: Watching folder...")
            self.log_message(f"Started watching: {self.watch_folder_entry.get()}")
            
            # Disable settings while watching
            self.client_code_entry.configure(state="disabled")
            self.watch_folder_entry.configure(state="disabled")
            self.output_folder_entry.configure(state="disabled")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start watching: {str(e)}")
    
    def stop_watching(self):
        """Stop watching the folder"""
        try:
            if self.observer:
                self.observer.stop()
                self.observer.join()
            
            self.is_watching = False
            self.start_button.configure(text="Start Watching")
            self.status_label.configure(text="Status: Not watching")
            self.log_message("Stopped watching folder")
            
            # Re-enable settings
            self.client_code_entry.configure(state="normal")
            self.watch_folder_entry.configure(state="normal")
            self.output_folder_entry.configure(state="normal")
            
        except Exception as e:
            self.log_message(f"Error stopping watcher: {str(e)}")
    
    def process_existing_files(self):
        """Process all existing files in the watch folder"""
        watch_folder = self.watch_folder_entry.get()
        
        if not watch_folder or not os.path.exists(watch_folder):
            messagebox.showerror("Error", "Please select a valid watch folder")
            return
        
        if not self.client_code_entry.get() or len(self.client_code_entry.get()) != 3:
            messagebox.showerror("Error", "Please enter a 3-letter client code")
            return
        
        if not self.output_folder_entry.get() or not os.path.exists(self.output_folder_entry.get()):
            messagebox.showerror("Error", "Please select a valid output folder")
            return
        
        # Process files in a separate thread
        thread = threading.Thread(target=self._process_existing_files_thread)
        thread.daemon = True
        thread.start()
    
    def _process_existing_files_thread(self):
        """Thread function to process existing files"""
        watch_folder = Path(self.watch_folder_entry.get())
        files = [f for f in watch_folder.iterdir() if f.is_file()]
        
        self.log_message(f"Processing {len(files)} existing files...")
        
        for file_path in files:
            self.process_file(file_path)
            time.sleep(0.1)  # Small delay to prevent overwhelming the system
        
        self.log_message("Finished processing existing files")


class FileHandler(FileSystemEventHandler):
    """Handle file system events"""
    
    def __init__(self, sorter):
        self.sorter = sorter
        self.processing_files = set()  # Track files being processed
    
    def on_created(self, event):
        """Handle file creation events"""
        if not event.is_directory:
            # Wait a moment for file to be fully written
            threading.Thread(target=self._process_with_delay, args=(event.src_path,)).start()
    
    def on_moved(self, event):
        """Handle file move events"""
        if not event.is_directory:
            # Process moved files (useful for files moved into watch folder)
            threading.Thread(target=self._process_with_delay, args=(event.dest_path,)).start()
    
    def _process_with_delay(self, file_path):
        """Process file with a delay to ensure it's fully written"""
        if file_path in self.processing_files:
            return
        
        self.processing_files.add(file_path)
        
        try:
            # Wait for file to be fully written
            time.sleep(1)
            
            # Check if file still exists and is not being written to
            if os.path.exists(file_path):
                initial_size = os.path.getsize(file_path)
                time.sleep(0.5)
                
                if os.path.exists(file_path) and os.path.getsize(file_path) == initial_size:
                    # File is stable, process it
                    self.sorter.process_file(file_path)
        
        finally:
            self.processing_files.discard(file_path)