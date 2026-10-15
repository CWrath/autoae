import os
import queue
import sys
import subprocess
import threading
import time
from tkinter import messagebox

import customtkinter as ctk

import app_updater
from app_version import APP_VERSION
import theme
from theme import C, font
from tool_loader import load_tools
from ffmpeg_setup import configure_ffmpeg
import ffmpeg_installer
import error_help

APP_NAME = "AUTO AE"
VERSION_TEXT = "Beta {} · Modular Edition".format(APP_VERSION)
ALL_TOOLS = "All Tools"
SHORTCUT_LABEL = "⌘F" if sys.platform == "darwin" else "Ctrl F"


class AutoAE(ctk.CTk):
    def __init__(self):
        error_help.install()              # error dialogs explain raw OS errors and name the file
        theme.apply()                     # theme + appearance mode BEFORE the window exists
        ctk.CTk.__init__(self)
        self.title(APP_NAME)
        theme.set_app_icon(self)

        # FFmpeg status now lives in the status bar instead of a startup popup
        self.ffmpeg_path = configure_ffmpeg()

        self.tools = sorted(load_tools(), key=lambda t: (getattr(t, "name", "") or "").lower())
        self.categories = [c for c in theme.CATEGORIES]
        if any(theme.tool_category(t) == theme.OTHER for t in self.tools):
            self.categories.append(theme.OTHER)

        self.tool_windows = {}
        self._closing = False
        self._update_queue = queue.Queue()
        self._update_check_active = False
        self._ffmpeg_install_queue = queue.Queue()
        self._ffmpeg_install_active = False
        self.current_category = ALL_TOOLS
        self.query = ""
        self.visible_tools = []
        self.columns = 3
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if sys.platform == "darwin":
            self.setup_mac_app()

        # Layout: sidebar | divider | content, status bar underneath
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.build_sidebar()
        ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=C["border"]).grid(row=0, column=1, sticky="ns")
        self.build_content()
        self.build_status_bar()

        self.select_category(ALL_TOOLS)
        self.center_window(1120, 760)
        self.minsize(880, 560)

        shortcut = "<Command-f>" if sys.platform == "darwin" else "<Control-f>"
        self.bind(shortcut, lambda e: self.search.focus())
        self.after(100, self.search.focus)

    # ------------------------------------------------------------------ layout
    def build_sidebar(self):
        side = ctk.CTkFrame(self, width=232, corner_radius=0, fg_color=C["sidebar"])
        side.grid(row=0, column=0, sticky="nsw")
        side.pack_propagate(False)

        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(22, 20))
        ctk.CTkLabel(brand, text="AE", width=36, height=36, corner_radius=10, fg_color=C["brand"],
                     text_color="#FFFFFF", font=font("heading")).pack(side="left")
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=(11, 0))
        ctk.CTkLabel(names, text=APP_NAME, font=font("heading"), anchor="w", height=18).pack(anchor="w")
        ctk.CTkLabel(names, text="Assistant editor toolkit", font=font("caption"),
                     text_color=C["text_muted"], anchor="w", height=16).pack(anchor="w")

        ctk.CTkLabel(side, text="TOOLS", font=font("overline"), text_color=C["text_subtle"],
                     anchor="w", height=20).pack(fill="x", padx=24, pady=(0, 4))

        self.nav_items = {}
        entries = [(ALL_TOOLS, "layout-grid", len(self.tools))]
        for category in self.categories:
            count = sum(1 for t in self.tools if theme.tool_category(t) == category)
            entries.append((category, theme.category_style(category)["icon"], count))
        for name, icon_name, count in entries:
            item = NavItem(side, name, icon_name, count, command=lambda n=name: self.select_category(n))
            item.pack(fill="x", padx=12, pady=1)
            self.nav_items[name] = item

        bottom = ctk.CTkFrame(side, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=18, pady=18)
        ctk.CTkLabel(bottom, text="APPEARANCE", font=font("overline"), text_color=C["text_subtle"],
                     anchor="w", height=20).pack(fill="x", padx=6, pady=(0, 6))
        self.mode_switch = ctk.CTkSegmentedButton(bottom, values=["System", "Light", "Dark"],
                                                  command=self.set_mode, font=font("caption"), height=30)
        self.mode_switch.set(theme.saved_mode())
        self.mode_switch.pack(fill="x")

    def build_content(self):
        content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        content.grid(row=0, column=2, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=(30, 28), pady=(24, 12))
        header.grid_columnconfigure(0, weight=1)
        self.page_title = ctk.CTkLabel(header, text=ALL_TOOLS, font=font("display"), anchor="w", height=28)
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_subtitle = ctk.CTkLabel(header, text="", font=font("body"), text_color=C["text_muted"],
                                          anchor="w", height=20)
        self.page_subtitle.grid(row=1, column=0, sticky="w")
        self.search = SearchField(header, "Search tools", self.on_search, self.open_first_result)
        self.search.grid(row=0, column=1, rowspan=2, sticky="e")

        self.scroll = ctk.CTkScrollableFrame(content, fg_color="transparent", corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=(18, 6), pady=(0, 6))
        self.scroll._parent_canvas.bind("<Configure>", self.on_resize, add="+")

        # Build every card once; filtering just re-grids them (fast while typing).
        self.section_labels = {
            category: ctk.CTkLabel(self.scroll, text=category.upper(), font=font("overline"),
                                   text_color=C["text_subtle"], anchor="w", height=20)
            for category in self.categories
        }
        self.cards = {tool.name: ToolCard(self.scroll, tool, self.open_tool_window) for tool in self.tools}
        self.empty_label = ctk.CTkLabel(self.scroll, text="", font=font("body"),
                                        text_color=C["text_muted"], height=60)

    def build_status_bar(self):
        ctk.CTkFrame(self, height=1, corner_radius=0, fg_color=C["border"]).grid(
            row=1, column=0, columnspan=3, sticky="ew")
        bar = ctk.CTkFrame(self, height=30, corner_radius=0, fg_color=C["sidebar"])
        bar.grid(row=2, column=0, columnspan=3, sticky="ew")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text=VERSION_TEXT, font=font("caption"), text_color=C["text_subtle"],
                     height=30).pack(side="left", padx=16)

        status = ctk.CTkFrame(bar, fg_color="transparent")
        status.pack(side="right", padx=16)
        self.ffmpeg_status_dot = ctk.CTkFrame(status, width=8, height=8, corner_radius=4)
        self.ffmpeg_status_dot.pack(side="left", padx=(0, 7))
        self.ffmpeg_status_label = ctk.CTkLabel(
            status, text="", font=font("caption"), text_color=C["text_muted"], height=30
        )
        self.ffmpeg_status_label.pack(side="left")
        self.ffmpeg_install_button = theme.Button(
            status, "Install FFmpeg", kind="secondary", height=24, width=112,
            command=self.install_ffmpeg,
        )
        self.ffmpeg_install_button.pack_forget()
        self._refresh_ffmpeg_status()

        ctk.CTkLabel(bar, text="{} tools".format(len(self.tools)), font=font("caption"),
                     text_color=C["text_subtle"], height=30).pack(side="right", padx=4)
        self.update_button = theme.Button(
            bar, "Check for Updates", kind="ghost", height=26, width=142,
            command=self.check_for_updates,
        )
        self.update_button.pack(side="right", padx=(8, 0))

    def check_for_updates(self):
        if self._update_check_active:
            return
        self._update_check_active = True
        self.update_button.configure(state="disabled", text="Checking…")
        self._update_queue = queue.Queue()
        threading.Thread(target=self._check_updates_worker, daemon=True).start()
        self.after(100, self._poll_update_queue)

    def _check_updates_worker(self):
        try:
            release = app_updater.check_for_update()
            self._update_queue.put(("checked", release))
        except Exception as exc:
            self._update_queue.put(("error", str(exc)))

    def _poll_update_queue(self):
        while True:
            try:
                event = self._update_queue.get_nowait()
            except queue.Empty:
                break

            if event[0] == "checked":
                release = event[1]
                if release is None:
                    self._finish_update_check()
                    messagebox.showinfo("Auto AE is up to date", "You're running Auto AE {}.".format(APP_VERSION))
                else:
                    self._offer_update(release)
            elif event[0] == "progress":
                received, total = event[1], event[2]
                percent = int(received * 100 / total) if total else 0
                self.update_button.configure(text="Downloading… {}%".format(percent))
            elif event[0] == "downloaded":
                self._install_downloaded_update(event[1], event[2], event[3], event[4])
            elif event[0] == "error":
                self._finish_update_check()
                messagebox.showerror("Update check failed", event[1])

        if self._update_check_active:
            self.after(100, self._poll_update_queue)

    def _offer_update(self, release):
        notes = release["notes"].strip()
        if len(notes) > 700:
            notes = notes[:697] + "..."
        prompt = "Auto AE {} is available (you have {}).".format(release["version"], APP_VERSION)
        if notes:
            prompt += "\n\nRelease notes:\n" + notes
        prompt += "\n\nDownload and install this update?"
        if not messagebox.askyesno("Auto AE update available", prompt):
            self._finish_update_check()
            return

        self.update_button.configure(text="Downloading…")
        threading.Thread(
            target=self._download_update_worker, args=(release,), daemon=True
        ).start()

    def _download_update_worker(self, release):
        try:
            asset_path = app_updater.download_release_asset(
                release,
                progress_callback=lambda received, total: self._update_queue.put(
                    ("progress", received, total)
                ),
            )
            bundle_path = None
            staged_bundle = None
            if sys.platform == "darwin":
                bundle_path = os.environ.get("AUTOAE_APP_BUNDLE")
                if not bundle_path or not os.path.isdir(bundle_path):
                    raise RuntimeError("Couldn't locate the installed Auto AE app to restart.")
                support_dir = os.environ.get("AUTOAE_SUPPORT_DIR")
                if not support_dir:
                    support_dir = os.path.join(
                        os.path.expanduser("~"), "Library", "Application Support", "AUTO_AE"
                    )
                staged_bundle = app_updater.stage_macos_update(
                    asset_path, support_dir, expected_version=release["version"]
                )
            self._update_queue.put(
                ("downloaded", asset_path, release, bundle_path, staged_bundle)
            )
        except Exception as exc:
            self._update_queue.put(("error", str(exc)))

    def _install_downloaded_update(self, asset_path, release, bundle_path, staged_bundle):
        try:
            if sys.platform == "win32":
                subprocess.Popen([asset_path])
                self._finish_update_check()
                self.after(1000, self.on_close)
                return

            app_updater.relaunch_macos_after_exit(bundle_path, staged_bundle, asset_path)
            self._finish_update_check()
            messagebox.showinfo(
                "Update ready",
                "Auto AE {} will be installed after the app closes, then it will reopen.".format(
                    release["version"]
                ),
            )
            self.after(250, self.on_close)
        except Exception as exc:
            self._finish_update_check()
            messagebox.showerror("Update failed", str(exc))

    def _finish_update_check(self):
        self._update_check_active = False
        self.update_button.configure(state="normal", text="Check for Updates")

    # ------------------------------------------------------------ behaviour
    def select_category(self, category):
        self.current_category = category
        for name, item in self.nav_items.items():
            item.set_selected(name == category)
        self.page_title.configure(text=category)
        self.layout_cards()
        self.scroll._parent_canvas.yview_moveto(0)

    def on_search(self, text):
        self.query = text.strip().lower()
        self.layout_cards()

    def open_first_result(self):
        if self.visible_tools:
            self.open_tool_window(self.visible_tools[0])

    def matches(self, tool):
        if not self.query:
            return True
        haystack = " ".join([tool.name, getattr(tool, "description", ""), theme.tool_category(tool)]).lower()
        return all(word in haystack for word in self.query.split())

    def on_resize(self, event):
        width = event.width / ctk.ScalingTracker.get_widget_scaling(self)
        columns = max(1, min(4, int(width // 255)))
        if columns != self.columns:
            self.columns = columns
            self.layout_cards()

    def layout_cards(self):
        for widget in self.scroll.winfo_children():
            widget.grid_forget()
        for column in range(4):
            active = column < self.columns
            self.scroll.grid_columnconfigure(column, weight=1 if active else 0,
                                             uniform="cards" if active else "")

        categories = self.categories if self.current_category == ALL_TOOLS else [self.current_category]
        groups = []
        for category in categories:
            tools = [t for t in self.tools if theme.tool_category(t) == category and self.matches(t)]
            if tools:
                groups.append((category, tools))
        self.visible_tools = [tool for _, tools in groups for tool in tools]

        count = len(self.visible_tools)
        if self.query:
            self.page_subtitle.configure(text="{} of {} tools match “{}”".format(
                count, len(self.tools), self.query))
        else:
            self.page_subtitle.configure(text="{} tool{}".format(count, "" if count == 1 else "s"))

        row = 0
        for category, tools in groups:
            if self.current_category == ALL_TOOLS:
                self.section_labels[category].grid(row=row, column=0, columnspan=self.columns,
                                                   sticky="w", padx=10, pady=(4 if row == 0 else 18, 4))
                row += 1
            for index, tool in enumerate(tools):
                self.cards[tool.name].grid(row=row + index // self.columns, column=index % self.columns,
                                           sticky="nsew", padx=6, pady=6)
            row += (len(tools) + self.columns - 1) // self.columns

        if not groups:
            self.empty_label.configure(text="No tools match “{}”".format(self.query))
            self.empty_label.grid(row=0, column=0, columnspan=self.columns, pady=40)

    def set_mode(self, mode):
        ctk.set_appearance_mode(mode)
        theme.save_mode(mode)
        for window in [self] + list(self.tool_windows.values()):
            if window.winfo_exists():
                theme.match_titlebar(window)

    def _set_appearance_mode(self, mode_string):
        """CustomTkinter calls this on every light/dark change: the Appearance buttons, or Windows
        itself when System is picked. The launcher's call comes before any tool window's.

        On Windows, CustomTkinter recolors the title bar by hiding the launcher and showing it again.
        Tk hides the tool windows along with it, and they stayed hidden because each one then saved
        "hidden" as the state to go back to. Show them again before that happens."""
        showing = [(window, window.state()) for window in getattr(self, "tool_windows", {}).values()
                   if window.winfo_exists() and window.state() != "withdrawn"]
        super()._set_appearance_mode(mode_string)
        for window, state in showing:
            if window.winfo_exists() and window.state() == "withdrawn":
                window.state(state)  # back to "normal" (or "zoomed") without stealing focus

    def show_ffmpeg_help(self):
        if sys.platform == "darwin":
            detail = ("Use the Install button in the status bar to download FFmpeg "
                      "directly into Auto AE. No Terminal or Homebrew is required.")
        else:
            detail = ("The bundled FFmpeg could not be found. Please install FFmpeg and add it to your system PATH.\n"
                      "See https://ffmpeg.org/download.html for instructions.")
        messagebox.showwarning("FFmpeg Not Found", "FFmpeg is required for some tools.\n\n" + detail)

    def _refresh_ffmpeg_status(self):
        ok = self.ffmpeg_path is not None
        self.ffmpeg_status_dot.configure(fg_color=C["success"] if ok else C["warning"])
        if ok:
            installed_copy = str(ffmpeg_installer.support_dir() / "ffmpeg")
            app_root = os.path.dirname(os.path.abspath(__file__))
            ffmpeg_path = str(self.ffmpeg_path)
            bundled = (
                ffmpeg_path.startswith(installed_copy + os.sep)
                or ffmpeg_path.startswith(app_root + os.sep)
            )
            self.ffmpeg_status_label.configure(
                text="FFmpeg ready · {}".format("Auto AE" if bundled else "system")
            )
            self.ffmpeg_install_button.pack_forget()
            return

        self.ffmpeg_status_label.configure(text="FFmpeg not found")
        if sys.platform == "darwin":
            self.ffmpeg_install_button.pack(side="left", padx=(8, 0))
        else:
            self.ffmpeg_install_button.pack_forget()
            theme.make_clickable(self.ffmpeg_status_label, self.show_ffmpeg_help)

    def install_ffmpeg(self):
        if self._ffmpeg_install_active:
            return
        self._ffmpeg_install_active = True
        self.ffmpeg_install_button.configure(state="disabled", text="Starting…")
        self._ffmpeg_install_queue = queue.Queue()
        threading.Thread(target=self._install_ffmpeg_worker, daemon=True).start()
        self.after(100, self._poll_ffmpeg_install)

    def _install_ffmpeg_worker(self):
        try:
            ffmpeg_installer.install_ffmpeg(
                progress_callback=lambda received, total: self._ffmpeg_install_queue.put(
                    ("progress", received, total)
                )
            )
            self._ffmpeg_install_queue.put(("installed",))
        except Exception as exc:
            self._ffmpeg_install_queue.put(("error", str(exc)))

    def _poll_ffmpeg_install(self):
        while True:
            try:
                event = self._ffmpeg_install_queue.get_nowait()
            except queue.Empty:
                break

            if event[0] == "progress":
                received, total = event[1], event[2]
                percent = int(received * 100 / total) if total else 0
                self.ffmpeg_install_button.configure(
                    text="Downloading {}%".format(percent)
                )
            elif event[0] == "installed":
                self._ffmpeg_install_active = False
                self.ffmpeg_path = configure_ffmpeg()
                self._refresh_ffmpeg_status()
                messagebox.showinfo(
                    "FFmpeg installed",
                    "FFmpeg and FFprobe are ready for Auto AE's media tools.",
                )
            elif event[0] == "error":
                self._ffmpeg_install_active = False
                self.ffmpeg_install_button.configure(
                    state="normal", text="Install FFmpeg"
                )
                messagebox.showerror("FFmpeg installation failed", event[1])

        if self._ffmpeg_install_active:
            self.after(100, self._poll_ffmpeg_install)

    def setup_mac_app(self):
        """Mac: ⌘Q goes through on_close so running tools stop cleanly, and clicking the
        Dock icon brings the window back if it was minimized."""
        self.createcommand("tk::mac::Quit", self.on_close)
        self.createcommand("tk::mac::ReopenApplication", self.reopen)

    def reopen(self):
        self.deiconify()
        self.lift()

    def center_window(self, width, height):
        self.update_idletasks()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(width, int(screen_w * 0.95)), min(height, int(screen_h * 0.9))
        x, y = max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 2)
        self.geometry("{}x{}+{}+{}".format(width, height, x, y))

    # ------------------------------------------------------------ tool windows
    def open_tool_window(self, tool):
        """Open a tool in a new window"""
        if self._closing:
            return

        if tool.name in self.tool_windows and self.tool_windows[tool.name].winfo_exists():
            window = self.tool_windows[tool.name]
            if window.state() in ("withdrawn", "iconic"):
                window.deiconify()  # lift() alone leaves a hidden or minimized window out of sight
            window.lift()
            window.focus()
        else:
            try:
                tool_window = tool.create_window(self)
                self.tool_windows[tool.name] = tool_window
                tool_window.transient(self)
                tool_window.lift()
                tool_window.focus_force()
                theme.set_app_icon(tool_window)

                def on_close():
                    close_window = getattr(tool, "close_window", None)
                    if close_window:
                        close_window()
                    if tool.name in self.tool_windows:
                        del self.tool_windows[tool.name]
                    tool_window.destroy()

                tool_window.protocol("WM_DELETE_WINDOW", on_close)
                if sys.platform == "darwin":
                    tool_window.bind("<Command-w>", lambda e: on_close())  # ⌘W closes the tool window

            except Exception as e:
                messagebox.showerror("Error", f"Failed to open {tool.name}: {str(e)}")

    def on_close(self):
        """Stop tool activity before shutting down Tk's event loop."""
        if self._closing:
            return
        self._closing = True

        for tool_name, tool_window in list(self.tool_windows.items()):
            # A tool that fails to close mustn't stop the app from closing.
            try:
                tool = next((loaded_tool for loaded_tool in self.tools if loaded_tool.name == tool_name), None)
                if tool is not None:
                    close_window = getattr(tool, "close_window", None)
                    if close_window:
                        close_window()
            except Exception as error:
                print(f"Error closing {tool_name}: {error}")
            try:
                if tool_window.winfo_exists():
                    tool_window.destroy()
            except Exception:
                pass
        self.tool_windows.clear()

        self.quit()
        self.destroy()


class NavItem(ctk.CTkFrame):
    """Sidebar row: icon, label, count. Highlights when selected."""

    def __init__(self, master, text, icon_name, count, command):
        ctk.CTkFrame.__init__(self, master, fg_color="transparent", corner_radius=8, height=36)
        self.selected = False
        self.icon_normal = theme.icon(icon_name, C["text_muted"], 18)
        self.icon_selected = theme.icon(icon_name, C["accent_text"], 18)
        self.icon_label = ctk.CTkLabel(self, text="", image=self.icon_normal, width=20, height=34)
        self.icon_label.pack(side="left", padx=(12, 10))
        self.text_label = ctk.CTkLabel(self, text=text, font=font("body"), text_color=C["text_muted"],
                                       anchor="w", height=34)
        self.text_label.pack(side="left", fill="x", expand=True)
        self.count_label = ctk.CTkLabel(self, text=str(count), font=font("caption"),
                                        text_color=C["text_subtle"], width=24, height=34)
        self.count_label.pack(side="right", padx=(0, 10))
        theme.make_clickable(self, command, self.on_hover)

    def on_hover(self, hovering):
        if not self.selected:
            self.configure(fg_color=C["sidebar_hover"] if hovering else "transparent")

    def set_selected(self, selected):
        self.selected = selected
        self.configure(fg_color=C["accent_soft"] if selected else "transparent")
        self.icon_label.configure(image=self.icon_selected if selected else self.icon_normal)
        self.text_label.configure(text_color=C["accent_text"] if selected else C["text_muted"],
                                  font=font("body_strong") if selected else font("body"))
        self.count_label.configure(text_color=C["accent_text"] if selected else C["text_subtle"])


class ToolCard(ctk.CTkFrame):
    """Launcher card: category icon tile, tool name, tool description."""

    def __init__(self, master, tool, command):
        ctk.CTkFrame.__init__(self, master, fg_color=C["surface"], corner_radius=theme.RADIUS,
                              border_width=1, border_color=C["border"])
        self.grid_columnconfigure(1, weight=1)
        theme.icon_tile(self, theme.tool_icon(tool), theme.tool_category(tool), size=38, icon_size=19).grid(
            row=0, column=0, rowspan=2, sticky="nw", padx=(14, 12), pady=14)
        self.name_label = ctk.CTkLabel(self, text=tool.name, font=font("heading"), anchor="w",
                                       justify="left", height=20, wraplength=170)
        self.name_label.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(15, 0))
        self.description = ctk.CTkLabel(self, text=getattr(tool, "description", ""), font=font("caption"),
                                        text_color=C["text_muted"], anchor="nw", justify="left",
                                        wraplength=170)
        self.description.grid(row=1, column=1, sticky="new", padx=(0, 14), pady=(2, 14))
        self.bind("<Configure>", self.rewrap, add="+")
        theme.make_clickable(self, lambda: command(tool), self.on_hover)

    def rewrap(self, event):
        width = event.width / ctk.ScalingTracker.get_widget_scaling(self)
        wrap = max(120, int(width - 14 - 38 - 12 - 14 - 4))
        for label in (self.name_label, self.description):
            if label.cget("wraplength") != wrap:
                label.configure(wraplength=wrap)

    def on_hover(self, hovering):
        self.configure(fg_color=C["hover"] if hovering else C["surface"],
                       border_color=C["border_strong"] if hovering else C["border"])


class SearchField(ctk.CTkFrame):
    """Entry with a search icon and a shortcut hint. Enter opens the top result, Esc clears."""

    def __init__(self, master, placeholder, on_change, on_submit, width=300):
        ctk.CTkFrame.__init__(self, master, width=width, height=36, corner_radius=8, border_width=1,
                              fg_color=C["field"], border_color=C["border_strong"])
        self.pack_propagate(False)
        ctk.CTkLabel(self, text="", image=theme.icon("search", C["text_subtle"], 16), width=18).pack(
            side="left", padx=(11, 2))
        ctk.CTkLabel(self, text=SHORTCUT_LABEL, font=font("caption"), text_color=C["text_subtle"],
                     fg_color=C["surface_2"], corner_radius=5, width=44, height=20).pack(side="right", padx=8)
        self.entry = ctk.CTkEntry(self, placeholder_text=placeholder, border_width=0, fg_color="transparent",
                                  height=30, font=font("body"))
        self.entry.pack(side="left", fill="both", expand=True, pady=2)
        self.entry.bind("<KeyRelease>", lambda e: on_change(self.entry.get()))
        self.entry.bind("<Return>", lambda e: on_submit())
        self.entry.bind("<Escape>", lambda e: (self.entry.delete(0, "end"), on_change("")))
        self.entry.bind("<FocusIn>", lambda e: self.configure(border_color=C["accent"]))
        self.entry.bind("<FocusOut>", lambda e: self.configure(border_color=C["border_strong"]))

    def focus(self):
        self.entry.focus_set()


def exit_process(grace_seconds=3.0):
    """Called once the window has closed. Background work a tool started (a transfer, a watcher)
    gets a few seconds to finish; after that Python exits anyway instead of running on unseen."""
    deadline = time.monotonic() + grace_seconds
    for thread in threading.enumerate():
        if thread is not threading.main_thread() and not thread.daemon:
            thread.join(max(0.0, deadline - time.monotonic()))
    if any(t.is_alive() and not t.daemon for t in threading.enumerate() if t is not threading.main_thread()):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        os._exit(0)


if __name__ == "__main__":
    # Add tools directory to path if not already there
    tools_path = os.path.join(os.path.dirname(__file__), 'tools')
    if tools_path not in sys.path:
        sys.path.insert(0, tools_path)

    app = AutoAE()
    app.mainloop()
    exit_process()
