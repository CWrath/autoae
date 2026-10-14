import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import os
import re
import shutil
import threading
import time
from base_tool import BaseTool
import theme
from theme import C
import error_help

try:
    import psutil
except ImportError:                 # optional: only used to spot FAT32 destinations
    psutil = None

FAT32_LIMIT = 4 * 1024 ** 3 - 1     # largest file FAT32 can hold
READ_RETRIES = 3                    # re-open and retry a failed read this many times


class CopyFailed(Exception):
    """One file couldn't be copied. `message` is the full explanation, `reason` a one-liner."""

    def __init__(self, message, reason):
        super().__init__(message)
        self.message = message
        self.reason = reason


def long_path(path):
    """On Windows, let paths of 260+ characters work by using the extended-length form."""
    if os.name != "nt":
        return path
    path = os.path.abspath(path)
    if len(path) < 240 or path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def filesystem_type(path):
    """Filesystem name of the drive holding `path` ("NTFS", "exFAT", "FAT32"...), or "" if unknown."""
    if psutil is None:
        return ""
    try:
        target = os.path.abspath(path).casefold()
        best, best_type = "", ""
        for part in psutil.disk_partitions(all=True):
            mount = os.path.abspath(part.mountpoint).casefold()
            if target.startswith(mount) and len(mount) > len(best):
                best, best_type = mount, part.fstype or ""
        return best_type
    except Exception:
        return ""


def format_size(size):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


def sanitize_folder_name(name):
    """Return a valid single folder name for Windows and macOS."""
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not sanitized:
        return "Consolidated Media"

    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if sanitized.casefold() in {reserved.casefold() for reserved in reserved_names}:
        sanitized = f"_{sanitized}"
    return sanitized[:255]


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Consolidate Media"
        self.description = "Collect and organize media files based on EDL and ALE lists"
        self.processing_state = "idle"
        self.processing_thread = None
        self.stop_event = threading.Event()
        
    def create_window(self, parent):
        window = self.create_tool_window(parent, "Consolidate Media", 900, 720)
        window.minsize(760, 600)

        page = ctk.CTkFrame(window, fg_color="transparent")
        page.pack(fill="both", expand=True, padx=24, pady=(22, 20))

        # Header: same icon tile + description the launcher card shows
        theme.tool_header(page, self).pack(fill="x", pady=(0, 18))

        # --- Sources ---------------------------------------------------------
        sources = theme.Card(page, title="Sources")
        sources.pack(fill="x")
        sources.body.grid_columnconfigure(0, weight=1)

        edl_field = theme.PathField(sources.body, "Edit lists", "One or more .edl or .ale files",
                                    command=self.browse_files_edl)
        edl_field.grid(row=0, column=0, sticky="ew")
        source_field = theme.PathField(sources.body, "Source media", "Folder to search for clips",
                                       command=lambda: self.browse_folder(self.source_entry))
        source_field.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        destination_field = theme.PathField(sources.body, "Destination", "Where the consolidated media goes",
                                            command=lambda: self.browse_folder(self.destination_entry))
        destination_field.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.edl_entry = edl_field.entry
        self.source_entry = source_field.entry
        self.destination_entry = destination_field.entry

        self.group_by_source_var = tk.BooleanVar(value=True)
        self.group_by_source_checkbox = ctk.CTkCheckBox(
            sources.body,
            text="Create subfolders based on original source folders",
            variable=self.group_by_source_var,
            onvalue=True,
            offvalue=False,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.group_by_source_checkbox.grid(row=3, column=0, sticky="w", padx=(132, 0), pady=(14, 0))

        # --- Progress + log --------------------------------------------------
        progress = theme.Card(page, title="Progress")
        progress.pack(fill="both", expand=True, pady=(14, 0))
        percent_label = ctk.CTkLabel(progress.header, text="0%", font=theme.font("caption"),
                                     text_color=C["text_muted"], height=20)
        percent_label.pack(side="right")

        self.progress_label = ctk.CTkLabel(progress.body, text="Ready to consolidate media", anchor="w", height=22)
        self.progress_label.pack(fill="x")
        self.progress_bar = theme.ProgressBar(progress.body, percent_label=percent_label)
        self.progress_bar.pack(fill="x", pady=(6, 12))

        # Same insert(tk.END, ...) / delete(0, tk.END) API as the old tk.Listbox
        self.results_listbox = theme.LogBox(
            progress.body, empty_text="Copied, skipped and missing clips will be listed here.")
        self.results_listbox.pack(fill="both", expand=True)

        # --- Actions: one primary, the rest quieter ---------------------------
        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.pack(fill="x", pady=(16, 0))
        self.consolidate_button = theme.Button(actions, "Consolidate Media", kind="primary", width=170,
                                               command=self.run_consolidation)
        self.consolidate_button.pack(side="right")
        self.stop_button = theme.Button(actions, "Stop", kind="danger", width=76, state="disabled",
                                        command=self.stop_consolidation)
        self.stop_button.pack(side="right", padx=(0, 8))
        self.pause_button = theme.Button(actions, "Pause", kind="secondary", width=90, state="disabled",
                                         command=self.toggle_pause)
        self.pause_button.pack(side="right", padx=(0, 8))
        theme.Button(actions, "Clear", kind="ghost", width=76,
                     command=self.clear_consolidation).pack(side="left")

        # Store window reference
        self.window = window
        self.ui_thread_id = threading.get_ident()

        return window
    
    def browse_file(self, entry_widget, file_type="EDL"):
        """Browse for a file and update entry widget"""
        if file_type == "EDL":
            file_path = filedialog.askopenfilename(
                title="Select EDL or ALE File",
                filetypes=[("EDL/ALE files", "*.edl *.ale"), ("All files", "*.*")]
            )
        else:
            file_path = filedialog.askopenfilename(title="Select File")
        if file_path:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, file_path)

    def browse_folder(self, entry_widget):
        """Browse for a folder and update entry widget"""
        folder_path = filedialog.askdirectory(title="Select Folder")
        if folder_path:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, folder_path)
    
    def browse_files_edl(self):
        """Browse for multiple EDL files and update entry widget"""
        file_paths = filedialog.askopenfilenames(
            title="Select EDL or ALE File(s)",
            filetypes=[("EDL/ALE files", "*.edl *.ale"), ("All files", "*.*")]
        )
        if file_paths:
            self.edl_entry.delete(0, tk.END)
            self.edl_entry.insert(0, ", ".join(file_paths))
    
    def run_consolidation(self):
        """Run the media consolidation process (EDL version, multi-file)"""
        edl_files = [f.strip() for f in self.edl_entry.get().split(",") if f.strip()]
        source_folder = self.source_entry.get().strip()
        destination_root = self.destination_entry.get().strip()
        group_by_source = bool(self.group_by_source_var.get())

        if not edl_files or not source_folder or not destination_root:
            messagebox.showerror("Error", "Please fill in all fields")
            return

        self.results_listbox.delete(0, tk.END)
        self.progress_bar.set(0)
        self.processing_state = "running"
        self.stop_event.clear()
        self.pause_button.configure(state="normal", text="Pause")
        self.stop_button.configure(state="normal")
        self.consolidate_button.configure(state="disabled")
        self.processing_thread = threading.Thread(
            target=self._run_consolidation_worker,
            args=(source_folder, edl_files, destination_root, group_by_source),
            daemon=True,
        )
        self.processing_thread.start()

    def _run_consolidation_worker(self, source_folder, edl_files, destination_root, group_by_source):
        try:
            result = self._consolidate(
                source_folder, edl_files, destination_root, group_by_source
            )
            status = "Stopped" if self.processing_state == "stopped" else "Complete"
            failed = result.get("failed", [])
            self._ui_call(
                self.progress_label.configure,
                text=f"{status}: {result['copied']} copied, {len(result['missing'])} missing"
                     + (f", {len(failed)} failed" if failed else ""),
            )
            if failed:
                self._ui_call(self.results_listbox.insert, tk.END,
                              f"Failed report: {result['failed_report']}")
                first = failed[0]
                more = (f"\n\n{len(failed) - 1} more file(s) also failed. All of them are listed in:\n"
                        f"{result['failed_report']}") if len(failed) > 1 else \
                    f"\n\nThis is also saved in:\n{result['failed_report']}"
                self._ui_call(
                    messagebox.showwarning, "Consolidate Media",
                    f"{len(failed)} file(s) couldn't be copied. The rest were.\n\n"
                    f"First problem:\n{first['message']}{more}")
            if result["missing"]:
                self._ui_call(
                    self.results_listbox.insert,
                    tk.END,
                    f"Missing report: {result['missing_report']}",
                )
        except Exception as exc:
            step, path = getattr(self, "_current_step", (None, None))
            if isinstance(exc, CopyFailed):
                message = exc.message
            elif isinstance(exc, OSError):
                message = error_help.explain(exc, path=path, action=step)
            elif step:
                message = f"Couldn't {step}." + (f"\nFile: {path}" if path else "") + f"\n\n{exc}"
            else:
                message = f"Consolidation stopped: {exc}"
            self._ui_call(self.results_listbox.insert, tk.END, "Error: " + message.splitlines()[0]
                          + (f" ({path})" if path else ""))
            self._ui_call(messagebox.showerror, "Consolidate Media", message)
            self._ui_call(
                self.progress_label.configure,
                text="Error occurred during consolidation",
            )
        finally:
            self.processing_state = "idle"
            self._ui_call(self.pause_button.configure, state="disabled", text="Pause")
            self._ui_call(self.stop_button.configure, state="disabled")
            self._ui_call(self.consolidate_button.configure, state="normal")

    def _consolidate(self, source_folder, edl_files, destination_root, group_by_source):
        self._current_step = (None, None)
        if not os.path.isdir(source_folder):
            raise FileNotFoundError(f"Source folder not found: {source_folder}")
        for edl_file in edl_files:
            if not os.path.isfile(edl_file):
                raise FileNotFoundError(f"EDL file not found: {edl_file}")

        wrapper_folder = self.extract_edl_title(edl_files[0])
        if not wrapper_folder:
            wrapper_folder = os.path.basename(os.path.normpath(source_folder))
        wrapper_folder = sanitize_folder_name(wrapper_folder)
        destination_folder = os.path.join(destination_root, wrapper_folder)
        self._current_step = ("create the destination folder", destination_folder)
        os.makedirs(destination_folder, exist_ok=True)
        self._current_step = ("read the edit list", edl_files[0] if len(edl_files) == 1 else None)

        source_names = sorted(
            {name for path in edl_files for name in self.parse_source_names(path)},
            key=str.casefold,
        )
        if not source_names:
            raise ValueError("No source clip names were found in the selected EDL files.")

        report_path = os.path.join(destination_folder, "edl_source_filenames.txt")
        self._current_step = ("write the list of source names", report_path)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(source_names) + "\n")
        self._ui_call(
            self.results_listbox.insert,
            tk.END,
            f"Found {len(source_names)} unique source filenames",
        )

        self._current_step = ("read the source folder", source_folder)
        source_index = self.index_source_files(source_folder)
        self._current_step = (None, None)
        missing_files = []
        failed_files = []
        copied = 0
        for index, filename in enumerate(source_names, start=1):
            if not self.wait_for_resume():
                self._ui_call(self.results_listbox.insert, tk.END, "Stopped by user.")
                break
            self._ui_call(self.progress_bar.set, (index - 1) / len(source_names))
            self._ui_call(
                self.progress_label.configure,
                text=f"Processing {index}/{len(source_names)}: {filename}",
            )
            matching_file = source_index.get(filename.casefold())
            if not matching_file:
                missing_files.append(filename)
                self._ui_call(self.results_listbox.insert, tk.END, f"Missing: {filename}")
                continue

            relative_path = self.get_destination_relative_path(
                source_folder, matching_file, group_by_source
            )
            safe_relative = self.safe_relative_path(relative_path)
            if safe_relative != relative_path:
                self._ui_call(self.results_listbox.insert, tk.END,
                              f"Renamed: {relative_path} -> {safe_relative} (characters Windows doesn't allow)")
                relative_path = safe_relative
            destination_path = os.path.join(destination_folder, relative_path)
            try:
                self._current_step = ("create the destination folder", os.path.dirname(destination_path))
                os.makedirs(long_path(os.path.dirname(destination_path)), exist_ok=True)
                exists = self.destination_file_exists(matching_file, destination_path)
                if not exists:
                    self.check_fits(matching_file, destination_folder)
            except (OSError, CopyFailed) as exc:
                if not os.path.isdir(source_folder):
                    raise self.source_gone(source_folder, matching_file) from exc
                self.record_failure(failed_files, relative_path, matching_file, exc,
                                    self._current_step[1], self._current_step[0])
                continue
            if exists:
                self._ui_call(
                    self.results_listbox.insert,
                    tk.END,
                    f"Exists, skipped: {relative_path}",
                )
                continue
            if not self.wait_for_resume():
                self._ui_call(
                    self.results_listbox.insert,
                    tk.END,
                    "Stopped before copying the next file.",
                )
                break
            self.copy_note = ""
            self._current_step = ("copy this clip", matching_file)
            try:
                finished = self.copy_file_with_stop(matching_file, destination_path)
            except (OSError, CopyFailed) as exc:
                if not os.path.isdir(source_folder):
                    raise self.source_gone(source_folder, matching_file) from exc
                self.record_failure(failed_files, relative_path, matching_file, exc,
                                    matching_file, "copy this clip")
                continue
            if not finished:
                self._ui_call(
                    self.results_listbox.insert,
                    tk.END,
                    "Stopped during the current file transfer.",
                )
                break
            copied += 1
            self._ui_call(self.results_listbox.insert, tk.END, f"Copied: {relative_path}")
            if self.copy_note:
                self._ui_call(self.results_listbox.insert, tk.END, f"  Note: {self.copy_note}")

        self._ui_call(self.progress_bar.set, 1)
        missing_report = ""
        if missing_files:
            missing_report = os.path.join(destination_folder, "missing_files_report.txt")
            self._current_step = ("write the missing files report", missing_report)
            with open(missing_report, "w", encoding="utf-8") as handle:
                handle.write("Missing Files Report\n" + "=" * 50 + "\n\n")
                handle.write("\n".join(missing_files) + "\n")
        failed_report = ""
        if failed_files:
            failed_report = os.path.join(destination_folder, "failed_files_report.txt")
            with open(failed_report, "w", encoding="utf-8") as handle:
                handle.write("Files That Couldn't Be Copied\n" + "=" * 50 + "\n\n")
                for item in failed_files:
                    handle.write(item["message"] + "\n\n" + "-" * 50 + "\n\n")
        self._current_step = (None, None)
        return {"copied": copied, "missing": missing_files, "missing_report": missing_report,
                "failed": failed_files, "failed_report": failed_report}

    # --- error handling -------------------------------------------------------
    @staticmethod
    def safe_relative_path(relative_path):
        """On Windows, replace characters it won't accept in each part of the destination path."""
        if os.name != "nt":
            return relative_path
        parts = re.split(r"[\\/]", relative_path)
        return os.path.join(*[error_help.safe_name(part) if part not in ("", ".", "..") else part
                              for part in parts])

    def check_fits(self, source_path, destination_folder):
        """Fail early, with a clear reason, when a 4 GB+ file is headed for a FAT32 drive."""
        size = os.path.getsize(long_path(source_path))
        if size <= FAT32_LIMIT:
            return
        fstype = filesystem_type(destination_folder)
        if fstype.upper() in {"FAT32", "FAT", "VFAT", "MSDOS"}:
            message = (f"Couldn't copy this clip.\nFile: {source_path}\n\n{error_help.MARKER}\n"
                       f"It is {format_size(size)}, and the destination drive is formatted {fstype}, "
                       f"which can't hold files of 4 GB or more.\nCopy to an exFAT, NTFS or APFS drive instead, "
                       f"or reformat the destination as exFAT.")
            raise CopyFailed(message, f"too big for a {fstype} drive ({format_size(size)}; the limit is 4 GB)")

    def record_failure(self, failed_files, relative_path, source_path, exc, path, action):
        if isinstance(exc, CopyFailed):
            message, reason = exc.message, exc.reason
        else:
            message = error_help.explain(exc, path=path, action=action)
            reason = error_help.short_reason(exc, path)
        failed_files.append({"relative_path": relative_path, "source": source_path,
                             "message": message, "reason": reason})
        self._ui_call(self.results_listbox.insert, tk.END, f"Failed: {relative_path} \u2014 {reason}")

    def source_gone(self, source_folder, current_file):
        return CopyFailed(
            f"Couldn't finish consolidating.\nFile: {current_file}\n\n{error_help.MARKER}\n"
            f"The source folder can't be reached any more:\n{source_folder}\n"
            f"The card or drive was probably unplugged, ejected or went to sleep. Reconnect it and run "
            f"again. Files already copied are kept and will be skipped.",
            "source drive disconnected")

    @staticmethod
    def destination_file_exists(source_path, destination_path):
        """Return whether copying would duplicate or overwrite existing media."""
        if os.path.exists(destination_path):
            return True
        try:
            return os.path.samefile(source_path, destination_path)
        except (FileNotFoundError, OSError):
            return False

    def copy_file_with_stop(self, source_path, destination_path):
        """Copy in chunks so Stop can cancel without leaving a partial destination.

        Returns False if stopped. Raises CopyFailed naming the file and the step that failed.
        A read that fails is retried after re-opening the source, which gets past brief
        card-reader or network hiccups. Keeping the original dates is best effort.
        """
        temporary_path = f"{destination_path}.autoae-partial"
        source_io, dest_io = long_path(source_path), long_path(temporary_path)
        self.copy_note = ""
        self.read_retries_used = 0          # across the whole file, so a flaky card can't loop forever
        offset = 0
        try:
            try:
                total = os.path.getsize(source_io)
                source = open(source_io, "rb")
            except OSError as exc:
                raise self._copy_failed(exc, source_path, "open the source clip") from exc
            try:
                try:
                    destination = open(dest_io, "wb")
                except OSError as exc:
                    raise self._copy_failed(exc, destination_path, "create the copy") from exc
                with destination:
                    while True:
                        if not self.wait_for_resume():
                            return False
                        chunk, source = self._read_chunk(source, source_io, source_path, offset, total)
                        if not chunk:
                            break
                        try:
                            destination.write(chunk)
                        except OSError as exc:
                            raise self._copy_failed(
                                exc, destination_path, "write the copy",
                                f"Stopped after {format_size(offset)} of {format_size(total)}.") from exc
                        offset += len(chunk)
            finally:
                source.close()
            self._keep_dates(source_io, dest_io)
            try:
                os.replace(dest_io, long_path(destination_path))
            except OSError as exc:
                raise self._copy_failed(exc, destination_path, "save the finished copy") from exc
            return True
        finally:
            if os.path.exists(dest_io):
                try:
                    os.remove(dest_io)
                except OSError:
                    pass

    def _read_chunk(self, source, source_io, source_path, offset, total):
        """Read the next 1 MB, re-opening the source and retrying if the drive hiccups."""
        for attempt in range(READ_RETRIES + 1):
            try:
                return source.read(1024 * 1024), source
            except OSError as exc:
                self.read_retries_used = getattr(self, "read_retries_used", 0) + 1
                if attempt == READ_RETRIES or self.stop_event.is_set() or self.read_retries_used > 10:
                    raise self._copy_failed(
                        exc, source_path, "read the source clip",
                        f"The read failed at {format_size(offset)} of {format_size(total)}, "
                        f"{READ_RETRIES + 1} times in a row.") from exc
                self._ui_call(self.results_listbox.insert, tk.END,
                              f"  Read error at {format_size(offset)}, retrying ({attempt + 1}/{READ_RETRIES})...")
                try:
                    source.close()
                except OSError:
                    pass
                time.sleep(1.0 * (attempt + 1))
                try:
                    source = open(source_io, "rb")
                    source.seek(offset)
                except OSError:
                    continue      # still unreachable; the next read attempt reports it
        return b"", source

    def _keep_dates(self, source_io, dest_io):
        """Copy dates/permissions. Some drives (exFAT, FAT32, network shares) refuse parts of this."""
        try:
            shutil.copystat(source_io, dest_io)
            return
        except OSError:
            pass
        try:
            stat = os.stat(source_io)
            os.utime(dest_io, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        except OSError as exc:
            self.copy_note = ("copied, but the destination drive wouldn't take the original "
                              f"modified date ({error_help.short_reason(exc)}). The media itself is fine.")

    @staticmethod
    def _copy_failed(exc, path, action, extra=None):
        hint = None
        if error_help.is_drive_error(exc) and not error_help.path_problems(path):
            hint = error_help.READ_HINT if action.startswith(("read", "open the source")) else error_help.WRITE_HINT
        return CopyFailed(error_help.explain(exc, path=path, action=action, extra=extra, hint=hint),
                          f"couldn't {action}: {error_help.short_reason(exc, path, hint)}")

    def _ui_call(self, callback, *args, **kwargs):
        """Run a Tk callback on the UI thread when consolidation runs in a worker."""
        if (
            hasattr(self, "ui_thread_id")
            and threading.get_ident() != self.ui_thread_id
            and hasattr(self.window, "after")
        ):
            self.window.after(0, lambda: callback(*args, **kwargs))
        else:
            callback(*args, **kwargs)

    def wait_for_resume(self):
        while self.processing_state == "paused" and not self.stop_event.is_set():
            time.sleep(0.05)
        return self.processing_state != "stopped" and not self.stop_event.is_set()

    def toggle_pause(self):
        if self.processing_state == "running":
            self.processing_state = "paused"
            self.pause_button.configure(text="Resume")
            self.results_listbox.insert(tk.END, "Paused. Press Resume to continue.")
        elif self.processing_state == "paused":
            self.processing_state = "running"
            self.pause_button.configure(text="Pause")
            self.results_listbox.insert(tk.END, "Resumed.")

    def stop_consolidation(self):
        if self.processing_state in {"running", "paused"}:
            self.processing_state = "stopped"
            self.stop_event.set()
            self.results_listbox.insert(tk.END, "Stop requested.")

    def get_destination_relative_path(self, source_folder, matching_file, group_by_source):
        """Return the relative path to be used inside the destination folder."""
        if not group_by_source:
            return os.path.basename(matching_file)
        rel_path = os.path.relpath(matching_file, source_folder)
        if rel_path == os.path.basename(matching_file):
            return rel_path
        return rel_path

    def parse_edl_for_source_names(self, edl_file):
        """Parse common EDL source-name formats and return normalized basenames."""
        source_names = set()
        with open(edl_file, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                match = re.search(
                    r"(?:FROM\s+CLIP\s+NAME|SOURCE\s+FILE)\s*:\s*(.+?)\s*$",
                    line,
                    re.IGNORECASE,
                )
                if match:
                    name = os.path.basename(match.group(1).strip().strip('"').replace("\\", "/"))
                    if name:
                        source_names.add(name)
        return sorted(source_names, key=str.casefold)

    def parse_source_names(self, source_list_file):
        """Extract media basenames from an EDL or Avid tab-delimited ALE file."""
        if os.path.splitext(source_list_file)[1].casefold() == ".ale":
            return self.parse_ale_for_source_names(source_list_file)
        return self.parse_edl_for_source_names(source_list_file)

    def parse_ale_for_source_names(self, ale_file):
        """Read ALE Name/Filepath columns from the Data section."""
        with open(
            ale_file, "r", encoding="utf-8-sig", errors="ignore", newline=""
        ) as handle:
            lines = [line.rstrip("\r\n") for line in handle]

        try:
            column_index = next(
                index for index, line in enumerate(lines)
                if line.strip().casefold() == "column"
            )
            data_index = next(
                index for index, line in enumerate(
                    lines[column_index + 1:], column_index + 1
                )
                if line.strip().casefold() == "data"
            )
        except StopIteration as exc:
            raise ValueError(
                f"Invalid ALE file: missing Column/Data sections: {ale_file}"
            ) from exc

        if column_index + 1 >= len(lines):
            raise ValueError(f"Invalid ALE file: missing column headers: {ale_file}")
        headers = lines[column_index + 1].split("\t")
        header_map = {
            header.strip().casefold(): index
            for index, header in enumerate(headers)
        }
        name_index = header_map.get("name")
        filepath_index = header_map.get("filepath")
        if name_index is None and filepath_index is None:
            raise ValueError(
                f"Invalid ALE file: expected Name or Filepath column: {ale_file}"
            )

        source_names = set()
        for line in lines[data_index + 1:]:
            if not line.strip():
                continue
            values = line.split("\t")
            filepath = (
                values[filepath_index].strip()
                if filepath_index is not None and filepath_index < len(values)
                else ""
            )
            name = (
                values[name_index].strip()
                if name_index is not None and name_index < len(values)
                else ""
            )
            media_reference = filepath or name
            if media_reference:
                source_names.add(
                    os.path.basename(
                        media_reference.replace("\\", "/")
                    ).strip().strip('"')
                )
        return sorted((name for name in source_names if name), key=str.casefold)

    def index_source_files(self, source_folder):
        """Index files by basename and extensionless stem for fast matching."""
        indexed_files = {}
        for root, _, files in os.walk(source_folder):
            for file_name in sorted(files):
                path = os.path.join(root, file_name)
                indexed_files.setdefault(file_name.casefold(), path)
                stem, _ = os.path.splitext(file_name)
                if stem:
                    indexed_files.setdefault(stem.casefold(), path)
        return indexed_files
    
    def clear_consolidation(self):
        """Clear the input fields, results, and reset the tool"""
        self.edl_entry.delete(0, tk.END)
        self.source_entry.delete(0, tk.END)
        self.destination_entry.delete(0, tk.END)
        self.results_listbox.delete(0, tk.END)
        self.progress_bar.set(0)
        self.progress_label.configure(text="Ready to consolidate media")
        self.processing_state = "idle"
        self.pause_button.configure(state="disabled", text="Pause")
        self.stop_button.configure(state="disabled")
    
    def find_matching_file(self, source_folder, filename):
        """Find a file in source folder that matches the filename exactly (case-insensitive)"""
        return self.index_source_files(source_folder).get(filename.casefold())
    
    def extract_edl_title(self, edl_file):
        """Extract the TITLE from an EDL file (returns None if not found)"""
        try:
            with open(edl_file, 'r') as f:
                for line in f:
                    if line.strip().startswith('TITLE:'):
                        return line.strip().split('TITLE:', 1)[1].strip()
        except Exception:
            pass
        return None