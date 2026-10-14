import customtkinter as ctk
from base_tool import BaseTool
import theme
from theme import C
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import psutil
import os
import sys
import json

def get_windows_drives():
    if sys.platform == "darwin":
        return get_mac_volumes()
    try:
        import win32api
        drives = []
        partitions = psutil.disk_partitions()
        for p in partitions:
            if 'cdrom' not in p.opts.lower():
                try:
                    drive_name = win32api.GetVolumeInformation(p.device)[0]
                    display_name = f"{p.device} ({drive_name})" if drive_name else p.device
                    drives.append({"display": display_name, "path": p.device})
                except:
                    drives.append({"display": p.device, "path": p.device})
        return drives
    except ImportError:
        # Fallback if win32api is not available
        return [{"display": p.device, "path": p.device} for p in psutil.disk_partitions() if 'cdrom' not in p.opts.lower()]

def get_mac_volumes():
    """The startup disk plus everything mounted in /Volumes: external drives, card readers, NAS shares."""
    boot_name = "Macintosh HD"
    try:
        for name in os.listdir("/Volumes"):
            if os.path.realpath(os.path.join("/Volumes", name)) == "/":
                boot_name = name  # the startup disk appears in /Volumes as a link to /
                break
    except OSError:
        pass

    drives = [{"display": boot_name, "path": "/"}]
    try:
        partitions = psutil.disk_partitions(all=True)  # all=True so network shares are included
    except Exception:
        partitions = []
    seen = {"/"}
    for p in partitions:
        mount = p.mountpoint
        name = mount[len("/Volumes/"):]
        if not mount.startswith("/Volumes/") or mount in seen or not name:
            continue
        if name.startswith(".") or "com.apple." in mount or os.path.realpath(mount) == "/":
            continue  # Time Machine snapshots and other system mounts
        seen.add(mount)
        drives.append({"display": name, "path": mount})
    return drives

class StorageMonitor(ctk.CTkFrame):
    CHARTS_PER_ROW = 4
    CHART_HEIGHT = 300  # Adjust as needed
    SAVE_FILE = "storage_monitor_selected_drives.json"
    SIZE_FILE = "storage_monitor_window_size.json"

    def __init__(self, master=None, tool=None):
        super().__init__(master, fg_color="transparent")
        self.refresh_interval = 60000  # 60 seconds
        self.drive_frames = {}
        self.drive_map = {}
        self.master = master  # Save parent window reference
        self._refresh_job = None

        if tool is not None:
            theme.tool_header(self, tool, subtitle="Drive space at a glance. Refreshes every minute.").pack(
                fill="x", padx=24, pady=(22, 14))

        # Top control bar
        control_frame = ctk.CTkFrame(self, fg_color="transparent")
        control_frame.pack(padx=24, pady=(0, 10), fill="x")

        drives = get_windows_drives()
        self.drive_map = {d["display"]: d["path"] for d in drives}
        self.drive_selector = ctk.CTkOptionMenu(control_frame, values=[d["display"] for d in drives],
                                                width=220, height=34, dynamic_resizing=False)
        self.drive_selector.pack(side="left")

        add_button = theme.Button(control_frame, "Add Drive", kind="secondary", width=110,
                                  image=theme.icon("plus", C["text"], 15), compound="left",
                                  command=self.add_selected_drive)
        add_button.pack(side="left", padx=(8, 0))

        # Chart area
        self.canvas = ctk.CTkCanvas(self, highlightthickness=0, bg=C["bg"][theme.mode_index()])
        self.scroll_frame = ctk.CTkFrame(self.canvas, fg_color=C["bg"], corner_radius=0)
        self.scroll_window = self.canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")

        self.scrollbar = ctk.CTkScrollbar(self, orientation="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.scrollbar.set)

        self.pack_propagate(False)
        self.canvas.pack(fill="both", expand=True, padx=14)
        self.scrollbar.pack(fill="x", padx=14, pady=(0, 6))

        self.scroll_frame.bind("<Configure>", self.on_frame_configure)
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        ctk.AppearanceModeTracker.add(self._on_appearance_change, self)

        # Load previously selected drives
        self.load_selected_drives()

        # Restore window size if available
        self.restore_window_size()

        # Bind to window resize event to save size
        if hasattr(self.master, "bind"):
            self.master.bind("<Configure>", self.on_window_resize)

        self._schedule_auto_refresh()

    def _on_appearance_change(self, _mode=None):
        """Charts are images, so redraw them in the new colors."""
        if not self.winfo_exists():
            return
        self.canvas.configure(bg=C["bg"][theme.mode_index()])
        for path in self.drive_frames:
            self.update_chart(path)

    def _schedule_auto_refresh(self):
        if self.winfo_exists():
            self._refresh_job = self.after(self.refresh_interval, self.auto_refresh)

    def close(self):
        ctk.AppearanceModeTracker.remove(self._on_appearance_change)
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None
        if hasattr(self.master, "unbind"):
            try:
                self.master.unbind("<Configure>")
            except Exception:
                pass

    def add_selected_drive(self):
        display = self.drive_selector.get()
        path = self.drive_map[display]
        if path not in self.drive_frames:
            self.add_drive_chart(path, display)
            self.resize_window_to_fit_charts()
            self.save_selected_drives()  # Save after adding

    def add_drive_chart(self, path, display_name):
        chart_count = len(self.drive_frames)
        row = chart_count // self.CHARTS_PER_ROW
        col = chart_count % self.CHARTS_PER_ROW

        frame = ctk.CTkFrame(self.scroll_frame, width=220, fg_color=C["surface"], border_width=1,
                             border_color=C["border"], corner_radius=theme.RADIUS)
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="n")

        label = ctk.CTkLabel(frame, text=display_name, font=theme.font("heading"), height=22)
        label.pack(padx=16, pady=(14, 0))

        chart_canvas = ctk.CTkFrame(frame, fg_color="transparent")
        chart_canvas.pack(padx=16, pady=(6, 0))

        info_label = ctk.CTkLabel(frame, text="", font=theme.font("caption"), text_color=C["text_muted"],
                                  justify="center")
        info_label.pack(padx=16, pady=(4, 0))

        remove_btn = theme.Button(frame, "Remove", kind="danger", width=90, height=30,
                                  command=lambda: self.remove_drive_chart(path))
        remove_btn.pack(pady=(10, 14))

        self.drive_frames[path] = {
            "frame": frame,
            "label": label,
            "info": info_label,
            "canvas": chart_canvas,
            "display": display_name
        }

        self.update_chart(path)

    def update_chart(self, path):
        try:
            total, used, free, percent = psutil.disk_usage(path)
        except Exception:
            return

        i = theme.mode_index()
        # accent normally, amber past 75%, red past 90%
        used_role = "danger" if percent > 90 else "warning" if percent > 75 else "accent"
        sizes = [used, free]
        colors = [C[used_role][i], C["surface_2"][i]]
        card_bg = C["surface"][i]

        scale = ctk.ScalingTracker.get_widget_scaling(self)
        fig = Figure(figsize=(1.7, 1.7), dpi=100 * scale)
        fig.patch.set_facecolor(card_bg)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor(card_bg)
        ax.pie(sizes, colors=colors, startangle=90, counterclock=False,
               wedgeprops=dict(width=0.28, edgecolor=card_bg, linewidth=2))
        ax.text(0, 0.08, f"{int(percent)}%", ha="center", va="center", fontsize=17, fontweight="bold",
                color=C["text"][i])
        ax.text(0, -0.22, "used", ha="center", va="center", fontsize=9, color=C["text_muted"][i])
        ax.set_aspect("equal")
        ax.axis("off")

        drive_ui = self.drive_frames[path]
        for widget in drive_ui["canvas"].winfo_children():
            widget.destroy()

        chart = FigureCanvasTkAgg(fig, master=drive_ui["canvas"])
        chart.draw()
        chart.get_tk_widget().configure(bg=card_bg, highlightthickness=0)
        chart.get_tk_widget().pack()

        drive_ui["info"].configure(
            text=f"{free // (1024**3):,} GB free of {total // (1024**3):,} GB\n{used // (1024**3):,} GB used"
        )

        if percent > 90:
            drive_ui["frame"].configure(border_color=C["danger"])
            drive_ui["label"].configure(text=f"{drive_ui['display']}  ·  almost full", text_color=C["danger"])
        else:
            drive_ui["frame"].configure(border_color=C["border"])
            drive_ui["label"].configure(text=drive_ui["display"], text_color=C["text"])

    def remove_drive_chart(self, path):
        if path in self.drive_frames:
            self.drive_frames[path]["frame"].destroy()
            del self.drive_frames[path]
            # Re-layout remaining charts
            for idx, (p, data) in enumerate(self.drive_frames.items()):
                row = idx // self.CHARTS_PER_ROW
                col = idx % self.CHARTS_PER_ROW
                data["frame"].grid_configure(row=row, column=col)
            self.resize_window_to_fit_charts()
            self.save_selected_drives()  # Save after removing

    def save_selected_drives(self):
        """Save the list of selected drive paths to a JSON file."""
        try:
            with open(self.SAVE_FILE, "w") as f:
                json.dump(list(self.drive_frames.keys()), f)
        except Exception as e:
            print(f"Failed to save selected drives: {e}")

    def load_selected_drives(self):
        """Load the list of selected drive paths from a JSON file."""
        try:
            with open(self.SAVE_FILE, "r") as f:
                paths = json.load(f)
            # Add charts for each path if still available
            for display, path in self.drive_map.items():
                if path in paths:
                    self.add_drive_chart(path, display)
        except Exception:
            pass  # Ignore if file doesn't exist or is invalid

    def resize_window_to_fit_charts(self):
        chart_count = len(self.drive_frames)
        rows = max(1, ((chart_count - 1) // self.CHARTS_PER_ROW) + 1)
        # Calculate new height: base height + chart area height
        base_height = 200  # Height for controls/title, adjust as needed
        chart_area_height = rows * (self.CHART_HEIGHT + 20) # Height for each row of charts
        new_height = base_height + chart_area_height
        # Optionally, set a min/max height
        new_height = max(600, min(new_height, 1200))
        desired_width = max(900, self.CHARTS_PER_ROW * 240)  # 240px per chart + padding
        # Resize the parent window
        if hasattr(self.master, "geometry"):
            cur_width = self.master.winfo_width()
            self.master.geometry(f"{desired_width}x{new_height}")

    def on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def on_canvas_configure(self, event):
        self.canvas.itemconfig(self.scroll_window, width=event.width)

    def auto_refresh(self):
        if not self.winfo_exists():
            return
        for path in self.drive_frames:
            self.update_chart(path)
        self._schedule_auto_refresh()

    def on_window_resize(self, event):
        # Only save if this is the main window and it's not minimized
        if event.widget == self.master and self.master.winfo_ismapped():
            self.save_window_size()

    def save_window_size(self):
        try:
            width = self.master.winfo_width()
            height = self.master.winfo_height()
            with open(self.SIZE_FILE, "w") as f:
                json.dump({"width": width, "height": height}, f)
        except Exception as e:
            print(f"Failed to save window size: {e}")

    def restore_window_size(self):
        try:
            if os.path.exists(self.SIZE_FILE):
                with open(self.SIZE_FILE, "r") as f:
                    size = json.load(f)
                # Set geometry if valid
                if "width" in size and "height" in size:
                    self.master.geometry(f"{size['width']}x{size['height']}")
        except Exception as e:
            print(f"Failed to restore window size: {e}")

class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Storage Monitor"
        self.description = "Monitor disk space and storage usage"
        self.category = "Storage & Backup"
        self.icon = "hard-drive"
        self.monitor = None
        
    def create_window(self, parent):
        window = self.create_tool_window(parent, "Storage Space Monitor", 1000, 560)
        
        # Configure window grid
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)
        
        # Create and add the StorageMonitor widget
        self.monitor = StorageMonitor(window, tool=self)
        self.monitor.grid(row=0, column=0, sticky="nsew")
        
        return window

    def close_window(self):
        if self.monitor:
            self.monitor.close()
            self.monitor = None