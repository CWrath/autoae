import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import hashlib
from pathlib import Path
import threading
from datetime import datetime
from base_tool import BaseTool
import theme
from theme import C

class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Drive Comparison"
        self.description = "Compare files between two drives or folders"
        self.category = "Storage & Backup"
        self.icon = "git-compare-arrows"
        self.comparison_thread = None
        self.stop_comparison = False
        
    def create_window(self, parent):
        window = self.create_tool_window(parent, "Drive Comparison Tool", 1000, 780)
        window.minsize(860, 600)
        theme.style_ttk(window)

        page = theme.page(window)
        theme.tool_header(
            page, self, subtitle="Compare files between two drives or folders to find differences"
        ).pack(fill="x", pady=(0, 18))

        # --- Actions ------------------------------------------------------------
        control_frame = theme.action_bar(page)
        self.export_btn = theme.Button(control_frame, "Export Results…", kind="secondary", width=140,
                                       command=self.export_results, state="disabled")
        self.export_btn.pack(side="left")
        self.compare_btn = theme.Button(control_frame, "Start Comparison", kind="primary", width=160,
                                        command=self.start_comparison)
        self.compare_btn.pack(side="right")
        self.stop_btn = theme.Button(control_frame, "Stop", kind="danger", width=76,
                                     command=self.stop_comparison_process, state="disabled")
        self.stop_btn.pack(side="right", padx=(0, 8))

        # --- Locations + options --------------------------------------------
        setup = theme.Card(page, title="Locations")
        setup.pack(fill="x")
        drives_frame = setup.body
        drives_frame.grid_columnconfigure(0, weight=1)

        source_field = theme.PathField(drives_frame, "Source", "Drive or folder 1",
                                       command=lambda: self.browse_folder(self.source_entry))
        source_field.grid(row=0, column=0, sticky="ew")
        target_field = theme.PathField(drives_frame, "Target", "Drive or folder 2",
                                       command=lambda: self.browse_folder(self.target_entry))
        target_field.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.source_entry = source_field.entry
        self.target_entry = target_field.entry

        theme.divider(drives_frame).grid(row=2, column=0, sticky="ew", pady=14)

        # Comparison method
        method_frame = ctk.CTkFrame(drives_frame, fg_color="transparent")
        method_frame.grid(row=3, column=0, sticky="ew")
        theme.muted(method_frame, "Compare by", role="body", width=120).pack(side="left", padx=(0, 12))

        self.method_var = tk.StringVar(value="name_size")
        methods = [
            ("Name Only", "name"),
            ("Name + Size", "name_size"),
            ("Name + Size + Date", "name_size_date"),
            ("Content (MD5 Hash)", "hash"),
            ("Full (All Checks)", "full")
        ]

        for text, value in methods:
            radio = ctk.CTkRadioButton(method_frame, text=text, variable=self.method_var,
                                       value=value, radiobutton_width=18, radiobutton_height=18)
            radio.pack(side="left", padx=(0, 14))

        # Options checkboxes
        checkbox_frame = ctk.CTkFrame(drives_frame, fg_color="transparent")
        checkbox_frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        theme.muted(checkbox_frame, "Options", role="body", width=120).pack(side="left", padx=(0, 12))

        self.recursive_var = tk.BooleanVar(value=True)
        recursive_check = ctk.CTkCheckBox(checkbox_frame, text="Include subfolders",
                                          variable=self.recursive_var, checkbox_width=18, checkbox_height=18)
        recursive_check.pack(side="left", padx=(0, 14))

        self.show_identical_var = tk.BooleanVar(value=False)
        show_identical_check = ctk.CTkCheckBox(checkbox_frame, text="Show identical files",
                                               variable=self.show_identical_var, checkbox_width=18, checkbox_height=18)
        show_identical_check.pack(side="left", padx=(0, 14))

        self.ignore_hidden_var = tk.BooleanVar(value=True)
        ignore_hidden_check = ctk.CTkCheckBox(checkbox_frame, text="Ignore hidden files",
                                              variable=self.ignore_hidden_var, checkbox_width=18, checkbox_height=18)
        ignore_hidden_check.pack(side="left")

        # --- Results --------------------------------------------------------------
        results = theme.Card(page, title="Results")
        results.pack(fill="both", expand=True, pady=(14, 0))
        percent_label = ctk.CTkLabel(results.header, text="0%", font=theme.font("caption"),
                                     text_color=C["text_muted"], height=20)
        percent_label.pack(side="right")

        self.progress_label = ctk.CTkLabel(results.body, text="Ready to compare", anchor="w", height=22)
        self.progress_label.pack(fill="x")
        self.progress_bar = theme.ProgressBar(results.body, percent_label=percent_label)
        self.progress_bar.pack(fill="x", pady=(6, 8))

        # Tabs (CTkTabview; kept on self.notebook like before)
        self.notebook = ctk.CTkTabview(results.body, anchor="nw", height=260)
        self.notebook.pack(fill="both", expand=True)

        # Summary tab
        summary_frame = self.notebook.add("Summary")
        self.summary_text = ctk.CTkTextbox(summary_frame, font=theme.font("mono"), wrap="word")
        self.summary_text.pack(fill="both", expand=True)

        # Different files tab
        different_frame = self.notebook.add("Different Files")
        different_frame.grid_columnconfigure(0, weight=1)
        different_frame.grid_rowconfigure(0, weight=1)

        # Taller rows so the two-line size/date cells are readable
        scale = ctk.ScalingTracker.get_widget_scaling(window)
        ttk.Style(window).configure("Compare.Treeview", rowheight=int(44 * scale))
        self.diff_tree = ttk.Treeview(different_frame, columns=("Status", "Source", "Target", "Reason"),
                                      show="tree headings", style="Compare.Treeview")
        self.diff_tree.grid(row=0, column=0, sticky="nsew")
        theme.add_scrollbar(self.diff_tree, different_frame)

        # Configure columns
        self.diff_tree.heading("#0", text="File")
        self.diff_tree.column("#0", width=260)
        self.diff_tree.heading("Status", text="Status")
        self.diff_tree.column("Status", width=90)
        self.diff_tree.heading("Source", text="Source Size/Date")
        self.diff_tree.column("Source", width=150)
        self.diff_tree.heading("Target", text="Target Size/Date")
        self.diff_tree.column("Target", width=150)
        self.diff_tree.heading("Reason", text="Difference")
        self.diff_tree.column("Reason", width=200)

        # Only in source tab
        source_only_frame = self.notebook.add("Only in Source")
        source_only_frame.grid_columnconfigure(0, weight=1)
        source_only_frame.grid_rowconfigure(0, weight=1)
        self.source_only_listbox = theme.ThemedListbox(source_only_frame, text_role="warning")
        self.source_only_listbox.grid(row=0, column=0, sticky="nsew")
        theme.add_scrollbar(self.source_only_listbox, source_only_frame)

        # Only in target tab
        target_only_frame = self.notebook.add("Only in Target")
        target_only_frame.grid_columnconfigure(0, weight=1)
        target_only_frame.grid_rowconfigure(0, weight=1)
        self.target_only_listbox = theme.ThemedListbox(target_only_frame, text_role="accent_text")
        self.target_only_listbox.grid(row=0, column=0, sticky="nsew")
        theme.add_scrollbar(self.target_only_listbox, target_only_frame)

        # Store window reference
        self.window = window

        # Store results for export
        self.comparison_results = None

        return window

    def browse_folder(self, entry_widget):
        """Browse for folder"""
        folder = filedialog.askdirectory(title="Select Folder or Drive")
        if folder:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, folder)
    
    def get_file_hash(self, filepath, block_size=65536):
        """Calculate MD5 hash of a file"""
        hasher = hashlib.md5()
        try:
            with open(filepath, 'rb') as f:
                while True:
                    data = f.read(block_size)
                    if not data:
                        break
                    hasher.update(data)
            return hasher.hexdigest()
        except Exception:
            return None
    
    def get_file_info(self, filepath):
        """Get file information"""
        try:
            stat = os.stat(filepath)
            return {
                'size': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime),
                'exists': True
            }
        except:
            return {'exists': False}
    
    def scan_directory(self, directory, recursive=True, ignore_hidden=True):
        """Scan directory and return file list"""
        files = {}
        
        try:
            if recursive:
                for root, dirs, filenames in os.walk(directory):
                    # Filter hidden directories if needed
                    if ignore_hidden:
                        dirs[:] = [d for d in dirs if not d.startswith('.')]
                    
                    for filename in filenames:
                        if ignore_hidden and filename.startswith('.'):
                            continue
                        
                        filepath = os.path.join(root, filename)
                        relative_path = os.path.relpath(filepath, directory)
                        files[relative_path] = filepath
            else:
                for item in os.listdir(directory):
                    if ignore_hidden and item.startswith('.'):
                        continue
                    
                    filepath = os.path.join(directory, item)
                    if os.path.isfile(filepath):
                        files[item] = filepath
        
        except Exception as e:
            self.progress_label.configure(text=f"Error scanning: {str(e)}")
        
        return files
    
    def compare_files(self, source_path, target_path, method):
        """Compare two files based on selected method"""
        source_info = self.get_file_info(source_path)
        target_info = self.get_file_info(target_path)
        
        if not source_info['exists'] or not target_info['exists']:
            return False, "File doesn't exist"
        
        differences = []
        
        if method in ['name_size', 'name_size_date', 'full']:
            if source_info['size'] != target_info['size']:
                differences.append(f"Size: {source_info['size']} vs {target_info['size']}")
        
        if method in ['name_size_date', 'full']:
            time_diff = abs((source_info['modified'] - target_info['modified']).total_seconds())
            if time_diff > 2:  # Allow 2 seconds difference
                differences.append(f"Modified time differs")
        
        if method in ['hash', 'full']:
            source_hash = self.get_file_hash(source_path)
            target_hash = self.get_file_hash(target_path)
            if source_hash != target_hash:
                differences.append("Content differs")
        
        is_identical = len(differences) == 0
        return is_identical, ", ".join(differences) if differences else "Identical"
    
    def start_comparison(self):
        """Start the comparison process"""
        source = self.source_entry.get()
        target = self.target_entry.get()
        
        if not source or not os.path.exists(source):
            messagebox.showerror("Error", "Please select a valid source location")
            return
        
        if not target or not os.path.exists(target):
            messagebox.showerror("Error", "Please select a valid target location")
            return
        
        # Disable controls during comparison
        self.compare_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.export_btn.configure(state="disabled")
        self.stop_comparison = False
        
        # Clear previous results
        self.clear_results()
        
        # Start comparison in separate thread
        self.comparison_thread = threading.Thread(target=self.run_comparison)
        self.comparison_thread.daemon = True
        self.comparison_thread.start()
    
    def run_comparison(self):
        """Run the actual comparison"""
        source = self.source_entry.get()
        target = self.target_entry.get()
        method = self.method_var.get()
        recursive = self.recursive_var.get()
        ignore_hidden = self.ignore_hidden_var.get()
        show_identical = self.show_identical_var.get()
        
        # Update progress
        self.progress_label.configure(text="Scanning source...")
        self.progress_bar.set(0.1)
        
        # Scan directories
        source_files = self.scan_directory(source, recursive, ignore_hidden)
        
        if self.stop_comparison:
            self.finish_comparison()
            return
        
        self.progress_label.configure(text="Scanning target...")
        self.progress_bar.set(0.2)
        
        target_files = self.scan_directory(target, recursive, ignore_hidden)
        
        if self.stop_comparison:
            self.finish_comparison()
            return
        
        # Prepare results
        results = {
            'identical': [],
            'different': [],
            'source_only': [],
            'target_only': [],
            'total_source': len(source_files),
            'total_target': len(target_files)
        }
        
        # Find files only in source
        for rel_path in source_files:
            if rel_path not in target_files:
                results['source_only'].append(rel_path)
        
        # Find files only in target
        for rel_path in target_files:
            if rel_path not in source_files:
                results['target_only'].append(rel_path)
        
        # Compare common files
        common_files = set(source_files.keys()) & set(target_files.keys())
        total_common = len(common_files)
        
        for idx, rel_path in enumerate(common_files):
            if self.stop_comparison:
                break
            
            # Update progress
            progress = 0.2 + (0.7 * (idx / total_common))
            self.progress_bar.set(progress)
            self.progress_label.configure(text=f"Comparing: {rel_path}")
            
            source_file = source_files[rel_path]
            target_file = target_files[rel_path]
            
            if method == 'name':
                # Files with same name are considered identical
                results['identical'].append({
                    'path': rel_path,
                    'source': source_file,
                    'target': target_file,
                    'reason': 'Same name'
                })
            else:
                is_identical, reason = self.compare_files(source_file, target_file, method)
                
                if is_identical:
                    results['identical'].append({
                        'path': rel_path,
                        'source': source_file,
                        'target': target_file,
                        'reason': reason
                    })
                else:
                    source_info = self.get_file_info(source_file)
                    target_info = self.get_file_info(target_file)
                    
                    results['different'].append({
                        'path': rel_path,
                        'source': source_file,
                        'target': target_file,
                        'reason': reason,
                        'source_info': source_info,
                        'target_info': target_info
                    })
        
        # Store results
        self.comparison_results = results
        
        # Display results
        self.window.after(0, self.display_results, results, show_identical)
        
        # Finish
        self.finish_comparison()
    
    def display_results(self, results, show_identical):
        """Display comparison results"""
        # Update summary
        summary = f"Comparison Results\n"
        summary += f"{'=' * 50}\n\n"
        summary += f"Source location: {self.source_entry.get()}\n"
        summary += f"Target location: {self.target_entry.get()}\n\n"
        summary += f"Total files in source: {results['total_source']}\n"
        summary += f"Total files in target: {results['total_target']}\n\n"
        summary += f"Identical files: {len(results['identical'])}\n"
        summary += f"Different files: {len(results['different'])}\n"
        summary += f"Files only in source: {len(results['source_only'])}\n"
        summary += f"Files only in target: {len(results['target_only'])}\n\n"
        
        if results['different']:
            summary += "Files with differences:\n"
            for item in results['different'][:10]:  # Show first 10
                summary += f"  • {item['path']}: {item['reason']}\n"
            if len(results['different']) > 10:
                summary += f"  ... and {len(results['different']) - 10} more\n"
        
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert("1.0", summary)
        
        # Update different files tree
        for item in self.diff_tree.get_children():
            self.diff_tree.delete(item)
        
        for diff in results['different']:
            source_info = f"{diff['source_info']['size']:,} bytes"
            if 'modified' in diff['source_info']:
                source_info += f"\n{diff['source_info']['modified'].strftime('%Y-%m-%d %H:%M')}"
            
            target_info = f"{diff['target_info']['size']:,} bytes"
            if 'modified' in diff['target_info']:
                target_info += f"\n{diff['target_info']['modified'].strftime('%Y-%m-%d %H:%M')}"
            
            self.diff_tree.insert("", "end", text=diff['path'],
                                values=("Different", source_info, target_info, diff['reason']))
        
        # Update source only listbox
        self.source_only_listbox.delete(0, tk.END)
        for path in sorted(results['source_only']):
            self.source_only_listbox.insert(tk.END, path)
        
        # Update target only listbox
        self.target_only_listbox.delete(0, tk.END)
        for path in sorted(results['target_only']):
            self.target_only_listbox.insert(tk.END, path)
        
        # Add identical files if requested
        if show_identical and results['identical']:
            for item in results['identical']:
                self.diff_tree.insert("", "end", text=item['path'],
                                    values=("Identical", "Match", "Match", item['reason']))
    
    def stop_comparison_process(self):
        """Stop the comparison process"""
        self.stop_comparison = True
        self.progress_label.configure(text="Stopping...")
    
    def finish_comparison(self):
        """Clean up after comparison"""
        self.progress_bar.set(1.0)
        self.compare_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        
        if self.comparison_results:
            self.export_btn.configure(state="normal")
            self.progress_label.configure(text="Comparison complete")
        else:
            self.progress_label.configure(text="Comparison stopped")
    
    def clear_results(self):
        """Clear all result displays"""
        self.summary_text.delete("1.0", tk.END)
        for item in self.diff_tree.get_children():
            self.diff_tree.delete(item)
        self.source_only_listbox.delete(0, tk.END)
        self.target_only_listbox.delete(0, tk.END)
    
    def export_results(self):
        """Export comparison results to file"""
        if not self.comparison_results:
            return
        
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save Comparison Results"
        )
        
        if file_path:
            try:
                if file_path.endswith('.csv'):
                    self.export_as_csv(file_path)
                else:
                    self.export_as_text(file_path)
                
                messagebox.showinfo("Success", f"Results exported to {file_path}")
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export: {str(e)}")
    
    def export_as_text(self, file_path):
        """Export results as text file"""
        with open(file_path, 'w', encoding='utf-8') as f:
            results = self.comparison_results
            
            f.write("Drive Comparison Report\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Source: {self.source_entry.get()}\n")
            f.write(f"Target: {self.target_entry.get()}\n")
            f.write(f"Method: {self.method_var.get()}\n\n")
            
            f.write("SUMMARY\n")
            f.write("-" * 30 + "\n")
            f.write(f"Total files in source: {results['total_source']}\n")
            f.write(f"Total files in target: {results['total_target']}\n")
            f.write(f"Identical files: {len(results['identical'])}\n")
            f.write(f"Different files: {len(results['different'])}\n")
            f.write(f"Files only in source: {len(results['source_only'])}\n")
            f.write(f"Files only in target: {len(results['target_only'])}\n\n")
            
            if results['different']:
                f.write("FILES WITH DIFFERENCES\n")
                f.write("-" * 30 + "\n")
                for item in results['different']:
                    f.write(f"\n{item['path']}\n")
                    f.write(f"  Reason: {item['reason']}\n")
                    f.write(f"  Source: {item['source']}\n")
                    f.write(f"  Target: {item['target']}\n")
            
            if results['source_only']:
                f.write("\n\nFILES ONLY IN SOURCE\n")
                f.write("-" * 30 + "\n")
                for path in sorted(results['source_only']):
                    f.write(f"{path}\n")
            
            if results['target_only']:
                f.write("\n\nFILES ONLY IN TARGET\n")
                f.write("-" * 30 + "\n")
                for path in sorted(results['target_only']):
                    f.write(f"{path}\n")
    
    def export_as_csv(self, file_path):
        """Export results as CSV file"""
        import csv
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Write header
            writer.writerow(['Status', 'File Path', 'Location', 'Size', 'Modified', 'Reason'])
            
            results = self.comparison_results
            
            # Write different files
            for item in results['different']:
                writer.writerow([
                    'Different',
                    item['path'],
                    'Both',
                    f"{item['source_info']['size']} / {item['target_info']['size']}",
                    'See reason',
                    item['reason']
                ])
            
            # Write source only files
            for path in results['source_only']:
                writer.writerow(['Missing in Target', path, 'Source Only', '', '', 'Not found in target'])
            
            # Write target only files
            for path in results['target_only']:
                writer.writerow(['Missing in Source', path, 'Target Only', '', '', 'Not found in source'])
            
            # Write identical files if included
            if self.show_identical_var.get():
                for item in results['identical']:
                    writer.writerow(['Identical', item['path'], 'Both', '', '', 'Files match'])