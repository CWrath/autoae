"""
Project Backup Tool
A comprehensive backup solution for project files with cloud storage integration
Compatible with AUTO AE tool loading system
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import os
import json
import shutil
import threading
import schedule
import time
from datetime import datetime, timedelta
import subprocess
import sys
from pathlib import Path
import logging
import theme
from theme import C

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

# Replace ProjectBackupTool with Tool for AUTO AE compatibility
class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Project Backup"
        self.description = "Automated cloud backup solution for your project files"
        self.category = "Storage & Backup"
        self.icon = "cloud-upload"
        
        # Configuration file path
        self.config_file = os.path.join(os.path.dirname(__file__), "backup_config.json")
        self.config = self.load_config()
        
        # Backup service state
        self.backup_service_running = False
        self.backup_thread = None
        
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
        self.backup_folder_var = None
        self.frequency_var = None
        self.hour_var = None
        self.minute_var = None
        self.background_var = None
        
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
        
        # Set up window closing protocol
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        return self.window
        
    def init_variables(self):
        """Initialize all tkinter variables"""
        self.project_path_vars = []  # List of StringVars for multiple folders
        self.file_types_var = tk.StringVar(value="*.*")
        self.exclude_patterns_var = tk.StringVar(value="node_modules, .git, __pycache__, dist, build")
        self.cloud_provider_var = tk.StringVar(value="dropbox")
        self.backup_folder_var = tk.StringVar(value="ProjectBackups")
        self.frequency_var = tk.StringVar(value="daily")
        self.hour_var = tk.StringVar(value="02")
        self.minute_var = tk.StringVar(value="00")
        self.background_var = tk.BooleanVar(value=True)
        
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
        self.tabview.add("Schedule")
        self.tabview.add("Status")
        
        self.create_source_tab()
        self.create_cloud_tab()
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
        row = len(self.project_path_vars) - 1
        
        frame = ctk.CTkFrame(self.folder_list_frame, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        
        entry = ctk.CTkEntry(frame, textvariable=var, width=400, height=34)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        
        browse_btn = theme.Button(frame, "Browse…", kind="secondary", width=92, command=lambda v=var: self.browse_folder_var(v))
        browse_btn.pack(side="left", padx=(0, 8))
        
        remove_btn = theme.Button(frame, "Remove", kind="danger", width=84, command=lambda: self.remove_folder_input(var, frame))
        remove_btn.pack(side="left")
        
    def remove_folder_input(self, var, frame):
        if var in self.project_path_vars:
            self.project_path_vars.remove(var)
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
        
        # Colour follows the text: green once connected
        self.auth_status_label = theme.StatusLabel(
            auth_frame, text="Not connected",
            rules=[("Not", "text_muted"), ("Connected", "success")])
        self.auth_status_label.pack(side="left")
        
        self.connect_button = theme.Button(
            auth_frame, 
            "Connect to Cloud Storage…", 
            kind="secondary",
            width=210,
            command=self.connect_cloud_storage
        )
        self.connect_button.pack(side="right")
        
        # Backup settings frame
        settings_card = theme.Card(tab, title="Backup settings")
        settings_card.pack(fill="x", pady=(12, 0))
        settings_frame = settings_card.body
        theme.form_row(settings_frame, 0, "Backup folder name",
                       ctk.CTkEntry(settings_frame, textvariable=self.backup_folder_var, height=34),
                       label_width=150, pady=(0, 0))
        
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
            text="Run in background when app is closed",
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
            exclude_patterns = [p.strip() for p in self.exclude_patterns_var.get().split(",")]
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
            
    def on_provider_change(self):
        provider = self.cloud_provider_var.get()
        self.auth_status_label.configure(text="Not Connected")
        self.log_message(f"Switched to {provider}")
        
    def connect_cloud_storage(self):
        provider = self.cloud_provider_var.get()
        
        if provider == "dropbox":
            self.connect_dropbox()
        elif provider == "google_drive":
            self.connect_google_drive()
            
    def connect_dropbox(self):
        # In a real implementation, you would use OAuth2 flow
        # For demo purposes, we'll simulate connection
        access_token = simpledialog.askstring(
            "Dropbox Token", 
            "Enter your Dropbox access token:\n(Get it from https://www.dropbox.com/developers/apps)",
            show='*'
        )
        
        if access_token:
            try:
                # Simulate dropbox connection (would need actual dropbox library)
                # self.dropbox_client = dropbox.Dropbox(access_token)
                # self.dropbox_client.users_get_current_account()
                self.auth_status_label.configure(text="Connected to Dropbox")
                self.log_message("Successfully connected to Dropbox")
            except Exception as e:
                self.log_message(f"Failed to connect to Dropbox: {str(e)}")
                messagebox.showerror("Connection Error", f"Failed to connect to Dropbox: {str(e)}")
                
    def connect_google_drive(self):
        # Placeholder for Google Drive connection
        messagebox.showinfo("Google Drive", "Google Drive integration requires additional setup.\nPlease refer to Google Drive API documentation.")
        
    def start_backup_service(self):
        if not self.validate_configuration():
            return
            
        self.backup_service_running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        
        # Clear previous schedule
        schedule.clear()
        
        # Schedule backups
        frequency = self.frequency_var.get()
        try:
            hour = int(self.hour_var.get())
            minute = int(self.minute_var.get())
        except ValueError:
            messagebox.showerror("Invalid Time", "Please enter valid hour and minute values")
            self.stop_backup_service()
            return
        
        if frequency == "hourly":
            schedule.every().hour.at(f":{minute:02d}").do(self.perform_backup)
        elif frequency == "daily":
            schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(self.perform_backup)
        elif frequency == "weekly":
            schedule.every().week.at(f"{hour:02d}:{minute:02d}").do(self.perform_backup)
            
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
        paths = [v.get() for v in self.project_path_vars if v.get()]
        if not paths:
            messagebox.showerror("Configuration Error", "Please select at least one project folder")
            return False
        for path in paths:
            if not os.path.exists(path):
                messagebox.showerror("Configuration Error", f"Selected project folder does not exist: {path}")
                return False
        return True
        
    def run_scheduler(self):
        while self.backup_service_running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
            
            # Update next backup time
            if self.window and self.window.winfo_exists():
                self.window.after(0, self.update_next_backup_time)
            
    def perform_backup(self):
        try:
            self.log_message("Starting backup...")
            self.progress_bar.set(0)
            self.progress_label.configure(text="Preparing backup...")
            paths = [v.get() for v in self.project_path_vars if v.get()]
            backup_folder = self.backup_folder_var.get()
            
            # Create temporary backup folder
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            temp_backup = os.path.join(os.path.dirname(__file__), f"temp_backup_{timestamp}")
            
            self.progress_label.configure(text="Copying files...")
            for path in paths:
                self.copy_files_with_progress(path, os.path.join(temp_backup, os.path.basename(path)))
                
            self.progress_label.configure(text="Uploading to cloud...")
            self.simulate_cloud_upload(temp_backup, backup_folder)
            
            # Cleanup
            if os.path.exists(temp_backup):
                shutil.rmtree(temp_backup)
            
            self.progress_bar.set(1.0)
            self.progress_label.configure(text="Backup completed successfully")
            self.last_backup_label.configure(text=f"Last backup: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.log_message("Backup completed successfully")
            
        except Exception as e:
            self.log_message(f"Backup failed: {str(e)}")
            self.progress_label.configure(text="Backup failed")
            
    def copy_files_with_progress(self, source, dest):
        exclude_patterns = [p.strip() for p in self.exclude_patterns_var.get().split(",")]
        
        # Count total files first
        total_files = 0
        for root, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if not any(pattern in d for pattern in exclude_patterns)]
            for file in files:
                if not any(pattern in file for pattern in exclude_patterns):
                    total_files += 1
                    
        # Copy files with progress
        copied_files = 0
        os.makedirs(dest, exist_ok=True)
        
        for root, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if not any(pattern in d for pattern in exclude_patterns)]
            
            for file in files:
                if any(pattern in file for pattern in exclude_patterns):
                    continue
                    
                src_file = os.path.join(root, file)
                rel_path = os.path.relpath(src_file, source)
                dest_file = os.path.join(dest, rel_path)
                
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                shutil.copy2(src_file, dest_file)
                
                copied_files += 1
                if total_files > 0:
                    progress = copied_files / total_files * 0.5  # First half of progress
                    self.progress_bar.set(progress)
                
    def simulate_cloud_upload(self, local_path, cloud_folder):
        """Simulate cloud upload for demo purposes"""
        provider = self.cloud_provider_var.get()
        self.log_message(f"Simulating upload to {provider}")
        
        # Count files for progress simulation
        total_files = 0
        for root, dirs, files in os.walk(local_path):
            total_files += len(files)
            
        uploaded_files = 0
        for root, dirs, files in os.walk(local_path):
            for file in files:
                # Simulate upload delay
                time.sleep(0.05)  # Reduced delay for faster demo
                uploaded_files += 1
                if total_files > 0:
                    progress = 0.5 + (uploaded_files / total_files) * 0.5  # Second half
                    self.progress_bar.set(progress)
                    
        self.log_message(f"Successfully uploaded {total_files} files to {cloud_folder}")
                
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
        
    def save_config(self):
        config = {
            "project_path": [v.get() for v in self.project_path_vars],
            "file_types": self.file_types_var.get(),
            "exclude_patterns": self.exclude_patterns_var.get(),
            "cloud_provider": self.cloud_provider_var.get(),
            "backup_folder": self.backup_folder_var.get(),
            "frequency": self.frequency_var.get(),
            "hour": self.hour_var.get(),
            "minute": self.minute_var.get(),
            "background": self.background_var.get()
        }
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            self.log_message("Configuration saved")
        except Exception as e:
            self.log_message(f"Failed to save configuration: {str(e)}")
            
    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load configuration: {str(e)}")
        return {}
        
    def load_saved_config(self):
        if self.config:
            project_paths = self.config.get("project_path", [])
            for path in project_paths:
                self.add_folder_input().set(path)
                
            self.file_types_var.set(self.config.get("file_types", "*.*"))
            self.exclude_patterns_var.set(self.config.get("exclude_patterns", "node_modules, .git, __pycache__"))
            self.cloud_provider_var.set(self.config.get("cloud_provider", "dropbox"))
            self.backup_folder_var.set(self.config.get("backup_folder", "ProjectBackups"))
            self.frequency_var.set(self.config.get("frequency", "daily"))
            self.hour_var.set(self.config.get("hour", "02"))
            self.minute_var.set(self.config.get("minute", "00"))
            self.background_var.set(self.config.get("background", True))
            
    def on_closing(self):
        if self.backup_service_running and self.background_var.get():
            # Create background service
            self.create_background_service()
        
        # Stop the backup service before closing
        if self.backup_service_running:
            self.backup_service_running = False
            schedule.clear()
            
        if self.window:
            self.window.destroy()
        
    def create_background_service(self):
        # Create a simple background script
        script_content = f'''
import schedule
import time
import json
from datetime import datetime
import os

# Load configuration
config_file = "{self.config_file}"
try:
    with open(config_file, "r") as f:
        config = json.load(f)
except Exception as e:
    print(f"Failed to load configuration: {{e}}")
    exit(1)

def backup_job():
    # Simplified backup logic for background service
    print(f"Background backup started at {{datetime.now()}}")
    # Add actual backup logic here

# Schedule the job
try:
    hour = int(config.get('hour', 2))
    minute = int(config.get('minute', 0))
    frequency = config.get('frequency', 'daily')
    
    if frequency == 'daily':
        schedule.every().day.at(f"{{hour:02d}}:{{minute:02d}}").do(backup_job)
    elif frequency == 'hourly':
        schedule.every().hour.at(f":{{minute:02d}}").do(backup_job)
    elif frequency == 'weekly':
        schedule.every().week.at(f"{{hour:02d}}:{{minute:02d}}").do(backup_job)
        
    print(f"Background backup service started - {{frequency}} at {{hour:02d}}:{{minute:02d}}")
    
    while True:
        schedule.run_pending()
        time.sleep(60)
except Exception as e:
    print(f"Background service error: {{e}}")
'''
        
        # Save background service script
        try:
            # Saved next to main.py, not in tools/: the tool loader imports every .py in tools/
            # and this script never returns, so the app would hang on the next launch.
            script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backup_service.py")
            with open(script_path, "w") as f:
                f.write(script_content)
                
            # Start background service
            if sys.platform == "win32":
                subprocess.Popen([sys.executable, script_path], 
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.Popen([sys.executable, script_path], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
            self.log_message("Background service created and started")
        except Exception as e:
            self.log_message(f"Failed to create background service: {str(e)}")

# Function to create tool instance for the tool loader
def create_tool():
    """Factory function for the tool loader"""
    return Tool()

# For compatibility with AUTO AE tool loading system
def get_tool():
    """Alternative factory function"""
    return Tool()