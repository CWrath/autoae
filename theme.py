"""
theme.py - one place for Auto AE's look.

    import theme
    theme.apply()          # call ONCE in main.py, before the CTk window is created

Everything visual lives here: color tokens, the type scale, icons, and a few
small widgets (Button, Card, PathField, ProgressBar, LogBox, ThemedListbox)
so every tool looks the same without copy-pasting colors and fonts.

Tools should NOT call ctk.set_appearance_mode() or ctk.set_default_color_theme();
the launcher owns those.
"""

import json
import os
import re
import sys
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
THEME_FILE = os.path.join(HERE, "autoae_theme.json")
ICON_DIR = os.path.join(HERE, "assets", "icons")
APP_ICON_ICO = os.path.join(HERE, "assets", "autoae.ico")
APP_ICON_PNG = os.path.join(HERE, "assets", "autoae.png")
# Settings live next to main.py, or next to the .exe when packaged with PyInstaller
_SETTINGS_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else HERE
SETTINGS_FILE = os.path.join(_SETTINGS_DIR, "autoae_settings.json")

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


# --------------------------------------------------------------------------
# Color tokens - (light, dark). Neutral grays on purpose: Resolve, Avid and
# Premiere all keep the chrome neutral so it doesn't color your judgement.
# The periwinkle from the old footer is the one accent.
# --------------------------------------------------------------------------
C = {
    "bg":            ("#F4F4F4", "#131313"),   # window background
    "sidebar":       ("#EBEBEB", "#181818"),   # sidebar + status bar
    "sidebar_hover": ("#E0E0E0", "#222222"),
    "surface":       ("#FFFFFF", "#1C1C1C"),   # cards
    "surface_2":     ("#F4F4F4", "#232323"),   # things sitting on cards
    "hover":         ("#F2F2F2", "#242424"),
    "field":         ("#FFFFFF", "#161616"),   # entries, logs, lists
    "control":       ("#FFFFFF", "#262626"),   # secondary buttons
    "control_hover": ("#F0F0F0", "#2E2E2E"),
    "border":        ("#E2E2E2", "#2A2A2A"),
    "border_strong": ("#CFCFCF", "#383838"),
    "text":          ("#1A1A1A", "#EDEDED"),
    "text_muted":    ("#5E5E5E", "#A3A3A3"),
    "text_subtle":   ("#6F6F6F", "#858585"),
    "text_disabled": ("#A8A8A8", "#6A6A6A"),
    "disabled_fill": ("#E4E4E4", "#2A2A2A"),
    "brand":         ("#6D80E7", "#6D80E7"),   # logo mark
    "accent":        ("#4A5ED4", "#5468D8"),   # primary buttons, selection
    "accent_hover":  ("#3F52C2", "#6275E0"),
    "accent_text":   ("#3F51C4", "#9AA6F2"),   # accent used as text/icon color
    "accent_soft":   ("#E6E9FB", "#262A3D"),   # selected nav item
    "success":       ("#1B7F50", "#4CC38A"),
    "warning":       ("#A86412", "#E5A84B"),
    "danger":        ("#C9353A", "#F0676B"),
    "danger_soft":   ("#FBE9E9", "#2E1C1D"),
}

# Categories drive the launcher sidebar and the icon-tile tint of each tool.
CATEGORIES = {
    "Project Setup":        {"icon": "folder-kanban",   "fg": ("#A2650E", "#E3B35E"), "bg": ("#FBEFD9", "#2D2719")},
    "Media":                {"icon": "clapperboard",    "fg": ("#4254C5", "#8E9CF0"), "bg": ("#E6E9FB", "#23273B")},
    "Editorial & Turnover": {"icon": "send-horizontal", "fg": ("#B83A6C", "#E58AAE"), "bg": ("#FBE6EE", "#33222B")},
    "Storage & Backup":     {"icon": "archive",         "fg": ("#17806A", "#56C2A9"), "bg": ("#DDF2EC", "#182B27")},
}
OTHER = "Other"
_NEUTRAL_TILE = {"icon": "wrench", "fg": C["text_muted"], "bg": C["surface_2"]}

# Fallback until each tool declares its own `self.category` / `self.icon`
# (BaseTool already has an `icon` attribute - put an icon name there).
DEFAULT_TOOL_META = {
    "Batch Renamer":                ("Media", "pencil-line"),
    "Bin Sync":                     ("Editorial & Turnover", "folder-sync"),
    "Consolidate Media":            ("Editorial & Turnover", "combine"),
    "Drive Comparison":             ("Storage & Backup", "git-compare-arrows"),
    "EDL/ALE/XML Converter":        ("Editorial & Turnover", "list-video"),
    "File Converter":               ("Media", "arrow-right-left"),
    "Folder Creator":               ("Project Setup", "folder-tree"),
    "Installers":                   ("Project Setup", "download"),
    "Media Browser":                ("Media", "film"),
    "Project Backup":               ("Storage & Backup", "cloud-upload"),
    "Proxy Generator":              ("Media", "layers"),
    "Storage Monitor":              ("Storage & Backup", "hard-drive"),
    "Subtitle to Markers":          ("Editorial & Turnover", "captions"),
    "Setup Kit":                    ("Project Setup", "keyboard"),
    "Timecode Calculator":          ("Editorial & Turnover", "timer"),
    "Watch Folder Sorter":          ("Media", "folder-input"),
}


def tool_category(tool):
    category = getattr(tool, "category", None)
    if category:
        return category
    return DEFAULT_TOOL_META.get(getattr(tool, "name", ""), (OTHER, None))[0]


def tool_icon(tool):
    icon_name = getattr(tool, "icon", None)
    if icon_name:
        return icon_name
    return DEFAULT_TOOL_META.get(getattr(tool, "name", ""), (None, "wrench"))[1] or "wrench"


def category_style(category):
    return CATEGORIES.get(category, _NEUTRAL_TILE)


# --------------------------------------------------------------------------
# Spacing + type scale
# --------------------------------------------------------------------------
PAD = 16        # inside cards
GAP = 12        # between related controls
RADIUS = 12     # cards

if IS_WINDOWS:
    MONO_FAMILY = "Consolas"
elif IS_MAC:
    MONO_FAMILY = "Menlo"
else:
    MONO_FAMILY = "DejaVu Sans Mono"

_TYPE_SCALE = {
    "display":     {"size": 22, "weight": "bold"},    # window / page titles
    "heading":     {"size": 14, "weight": "bold"},    # card titles, tool names
    "body_strong": {"size": 13, "weight": "bold"},    # buttons, emphasis
    "body":        {"size": 13},
    "caption":     {"size": 12},                      # descriptions, status bar
    "overline":    {"size": 11, "weight": "bold"},    # UPPERCASE section labels
    "mono":        {"size": 12, "family": MONO_FAMILY},
}
_fonts = {}


def font(role):
    """Shared CTkFont for a role in the type scale (create after the window exists)."""
    if role not in _fonts:
        _fonts[role] = ctk.CTkFont(**_TYPE_SCALE[role])
    return _fonts[role]


def sized_font(size, weight="normal", mono=False):
    """One-off sizes (e.g. a big timecode readout) that still use the theme's families."""
    key = ("sized", size, weight, mono)
    if key not in _fonts:
        options = {"size": size, "weight": weight}
        if mono:
            options["family"] = MONO_FAMILY
        _fonts[key] = ctk.CTkFont(**options)
    return _fonts[key]


# --------------------------------------------------------------------------
# Setup + persistence
# --------------------------------------------------------------------------
def _read_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def saved_mode():
    mode = _read_settings().get("appearance_mode", "System")
    return mode if mode in ("System", "Light", "Dark") else "System"


def save_mode(mode):
    settings = _read_settings()
    settings["appearance_mode"] = mode
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
    except OSError:
        pass


def apply(mode=None):
    """Load the Auto AE theme. Must run before the first CTk window is created."""
    ctk.set_appearance_mode(mode or saved_mode())
    # Falls back to the stock theme if the JSON wasn't bundled (e.g. a PyInstaller build without --add-data)
    ctk.set_default_color_theme(THEME_FILE if os.path.isfile(THEME_FILE) else "blue")
    _fix_mac_tk9_scrolling()


# --------------------------------------------------------------------------
# Mac + Tk 9 scrolling. CustomTkinter 5.2.2 was written for Tk 8.6. On a Mac,
# Tk 9 (what Homebrew's python-tk installs) reports each mouse-wheel click as
# 120 and trackpad / Magic Mouse scrolling as a separate <TouchpadScroll>
# event, so scrollable frames jumped a whole page per click and ignored the
# trackpad. This teaches CTkScrollableFrame both. Tk 8.6 and Windows are
# left alone.
# --------------------------------------------------------------------------
WHEEL_STEP = 32  # pixels per mouse-wheel click


def _touchpad_deltas(delta):
    """Split a <TouchpadScroll> delta into (dx, dy) pixels, like Tk's tk::PreciseScrollDeltas."""
    low = delta & 0xFFFF
    return delta >> 16, (low - 0x10000 if low >= 0x8000 else low)


def _scroll_canvas(canvas, axis, pixels):
    """Scroll a canvas by pixels. Canvases only scroll by whole units, which is too coarse for a trackpad."""
    first, last = canvas.xview() if axis == "x" else canvas.yview()
    if (first, last) == (0.0, 1.0):
        return  # everything fits
    region = [float(v) for v in canvas.tk.splitlist(canvas.cget("scrollregion"))]
    if len(region) != 4:
        return
    size = region[2] - region[0] if axis == "x" else region[3] - region[1]
    if size <= 0:
        return
    target = max(0.0, min(first + pixels / size, 1.0 - (last - first)))
    (canvas.xview_moveto if axis == "x" else canvas.yview_moveto)(target)


def _fix_mac_tk9_scrolling(force=False):
    frame_class = ctk.CTkScrollableFrame
    if getattr(frame_class, "_autoae_tk9_scrolling", False):
        return
    if not force and not (IS_MAC and tk.TkVersion >= 9.0):
        return
    original_init = frame_class.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.bind_all("<TouchpadScroll>", self._touchpad_scroll_all, add="+")

    def _inside(self, widget):
        try:
            return self.check_if_master_is_canvas(widget)
        except AttributeError:  # Tk-internal widgets arrive as plain names
            return False

    def _mouse_wheel_all(self, event):
        if _inside(self, event.widget):
            horizontal = self._shift_pressed or bool(event.state & 0x1)  # Tk 9 sends sideways scrolls with Shift
            _scroll_canvas(self._parent_canvas, "x" if horizontal else "y", -event.delta / 120 * WHEEL_STEP)

    def _touchpad_scroll_all(self, event):
        if _inside(self, event.widget):
            dx, dy = _touchpad_deltas(event.delta)
            if dx:
                _scroll_canvas(self._parent_canvas, "x", -dx)
            if dy:
                _scroll_canvas(self._parent_canvas, "y", -dy)

    frame_class.__init__ = __init__
    frame_class._mouse_wheel_all = _mouse_wheel_all
    frame_class._touchpad_scroll_all = _touchpad_scroll_all
    frame_class._autoae_tk9_scrolling = True


def mode_index():
    """0 for light, 1 for dark - for plain Tk widgets that need a single color."""
    return 1 if ctk.get_appearance_mode() == "Dark" else 0


def set_app_icon(window):
    """Replace the CustomTkinter logo in the title bar / taskbar."""
    try:
        if IS_WINDOWS and os.path.exists(APP_ICON_ICO):
            if isinstance(window, ctk.CTkToplevel):
                # CTkToplevel sets its own icon 200 ms after creation, so go after it.
                window.after(250, lambda: window.winfo_exists() and window.iconbitmap(APP_ICON_ICO))
            else:
                window.iconbitmap(APP_ICON_ICO)
        elif os.path.exists(APP_ICON_PNG) and not isinstance(window, ctk.CTkToplevel):
            window._app_icon_image = tk.PhotoImage(file=APP_ICON_PNG)
            window.iconphoto(True, window._app_icon_image)
    except tk.TclError:
        pass
    match_titlebar(window)


def match_titlebar(window):
    """Mac: macOS draws the title bar, so tell it when Light or Dark is picked instead of System."""
    if not IS_MAC:
        return
    if ctk.AppearanceModeTracker.appearance_mode_set_by == "system":
        appearance = "auto"
    else:
        appearance = "darkaqua" if ctk.get_appearance_mode() == "Dark" else "aqua"
    try:
        window.wm_attributes("-appearance", appearance)
    except tk.TclError:
        try:
            window.tk.call("::tk::unsupported::MacWindowStyle", "appearance", window._w, appearance)
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# Icons - white-on-transparent PNG masks in assets/icons (Lucide, ISC license),
# tinted at runtime so one file works for any color and both modes.
# --------------------------------------------------------------------------
_icons = {}


def icon(name, color=None, size=18):
    color = color or C["text_muted"]
    light, dark = (color, color) if isinstance(color, str) else color
    key = (name, light, dark, size)
    if key not in _icons:
        path = name if os.path.isfile(str(name)) else os.path.join(ICON_DIR, "{}.png".format(name))
        if not os.path.isfile(path):
            path = os.path.join(ICON_DIR, "wrench.png")
        if os.path.isfile(path):
            mask = Image.open(path).convert("RGBA").getchannel("A")
        else:                                   # assets missing: draw nothing rather than crash
            mask = Image.new("L", (64, 64), 0)

        def tint(hex_color):
            image = Image.new("RGBA", mask.size, hex_color)
            image.putalpha(mask)
            return image

        _icons[key] = ctk.CTkImage(light_image=tint(light), dark_image=tint(dark), size=(size, size))
    return _icons[key]


def icon_tile(master, icon_name, category=None, size=36, icon_size=18):
    """Rounded, category-tinted square holding an icon."""
    style = category_style(category)
    return ctk.CTkLabel(master, text="", image=icon(icon_name, style["fg"], icon_size),
                        width=size, height=size, fg_color=style["bg"], corner_radius=9)


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------
_BUTTON_KINDS = {
    # one per window: the main action
    "primary":   {"fg_color": C["accent"], "hover_color": C["accent_hover"],
                  "text_color": ("#FFFFFF", "#FFFFFF"), "border_width": 0},
    # Browse, Add, Save...
    "secondary": {"fg_color": C["control"], "hover_color": C["control_hover"], "text_color": C["text"],
                  "border_width": 1, "border_color": C["border_strong"]},
    # Clear, Cancel
    "ghost":     {"fg_color": "transparent", "hover_color": C["hover"], "text_color": C["text_muted"],
                  "border_width": 0},
    # Stop, Delete, Remove
    "danger":    {"fg_color": C["control"], "hover_color": C["danger_soft"], "text_color": C["danger"],
                  "border_width": 1, "border_color": C["border_strong"]},
}


class Button(ctk.CTkButton):
    """CTkButton with a role: primary | secondary | ghost | danger.

    Primary buttons also look disabled when disabled (stock CTk only dims the text).
    """

    def __init__(self, master, text="", kind="secondary", **kwargs):
        style = dict(_BUTTON_KINDS[kind])
        style.update(kwargs)
        style.setdefault("height", 34)
        style.setdefault("font", font("body_strong"))
        style.setdefault("text_color_disabled", C["text_disabled"])
        self._kind = kind
        self._enabled_fg = style["fg_color"]
        super().__init__(master, text=text, **style)
        self._restyle_for_state(self.cget("state"))

    def configure(self, require_redraw=False, **kwargs):
        if "fg_color" in kwargs:
            self._enabled_fg = kwargs["fg_color"]
        super().configure(require_redraw=require_redraw, **kwargs)
        if "state" in kwargs:
            self._restyle_for_state(kwargs["state"])

    def _restyle_for_state(self, state):
        if self._kind == "primary":
            fill = C["disabled_fill"] if str(state) == "disabled" else self._enabled_fg
            super().configure(fg_color=fill)


class Card(ctk.CTkFrame):
    """A bordered surface with an optional UPPERCASE title and helper line.

    Put content in `card.body`; put trailing header items (a count, a %) in `card.header`.
    """

    def __init__(self, master, title=None, subtitle=None, padding=PAD, **kwargs):
        kwargs.setdefault("fg_color", C["surface"])
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", C["border"])
        kwargs.setdefault("corner_radius", RADIUS)
        super().__init__(master, **kwargs)
        self.header = None
        if title:
            self.header = ctk.CTkFrame(self, fg_color="transparent", height=20)
            self.header.pack(fill="x", padx=padding, pady=(padding - 2, 0))
            ctk.CTkLabel(self.header, text=title.upper(), font=font("overline"),
                         text_color=C["text_subtle"], anchor="w", height=20).pack(side="left")
        if subtitle:
            ctk.CTkLabel(self, text=subtitle, font=font("caption"), text_color=C["text_muted"],
                         anchor="w", justify="left", height=18).pack(
                fill="x", padx=padding, pady=(2 if title else padding - 2, 0))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=padding,
                       pady=(10 if (title or subtitle) else padding, padding))


def page(window, padx=24, pady=(22, 20)):
    """Transparent content area with the standard window padding."""
    frame = ctk.CTkFrame(window, fg_color="transparent")
    frame.pack(fill="both", expand=True, padx=padx, pady=pady)
    return frame


def action_bar(master, pady=(16, 0)):
    """Bottom row for a window's buttons: ghost/clear on the left, main action far right."""
    bar = ctk.CTkFrame(master, fg_color="transparent")
    bar.pack(fill="x", side="bottom", pady=pady)
    return bar


def overline(master, text, **kwargs):
    """Small UPPERCASE label for a group inside a card."""
    kwargs.setdefault("anchor", "w")
    kwargs.setdefault("height", 20)
    return ctk.CTkLabel(master, text=text.upper(), font=font("overline"), text_color=C["text_subtle"], **kwargs)


def muted(master, text="", role="caption", **kwargs):
    """Secondary text: status lines, hints, helper copy."""
    kwargs.setdefault("anchor", "w")
    kwargs.setdefault("justify", "left")
    return ctk.CTkLabel(master, text=text, font=font(role), text_color=C["text_muted"], **kwargs)


def form_row(master, row, label, widget, label_width=120, pady=(0, 10), sticky="ew"):
    """Grid a muted label + control pair; `widget` must already have `master` as its parent."""
    master.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(master, text=label, width=label_width, anchor="w", font=font("body"),
                 text_color=C["text_muted"]).grid(row=row, column=0, sticky="w", padx=(0, GAP), pady=pady)
    widget.grid(row=row, column=1, sticky=sticky, pady=pady)
    return widget


def divider(master, **kwargs):
    kwargs.setdefault("fg_color", C["border"])
    return ctk.CTkFrame(master, height=1, corner_radius=0, **kwargs)


def center(window, width, height):
    """Size a window (CTk units), keep it on screen, and center it.

    CTk scales width/height by the display scaling but not x/y, so the position is
    worked out in physical pixels. Clamping keeps tall tools usable on a 1080p screen
    at 150% scaling.
    """
    try:
        scale = ctk.ScalingTracker.get_window_scaling(window)
    except Exception:
        scale = 1.0
    screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
    width = int(min(width, screen_w * 0.95 / scale))
    height = int(min(height, screen_h * 0.88 / scale))
    x = max(0, int((screen_w - width * scale) / 2))
    y = max(0, int((screen_h - height * scale) / 2.3))
    window.geometry("{}x{}+{}+{}".format(width, height, x, y))


def tool_header(master, tool=None, title=None, subtitle=None):
    """Left-aligned window header: category icon tile, title, one-line description."""
    title = title or getattr(tool, "name", "")
    subtitle = getattr(tool, "description", "") if subtitle is None else subtitle
    row = ctk.CTkFrame(master, fg_color="transparent")
    if tool is not None:
        icon_tile(row, tool_icon(tool), tool_category(tool), size=44, icon_size=22).pack(
            side="left", padx=(0, 14))
    text = ctk.CTkFrame(row, fg_color="transparent")
    text.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(text, text=title, font=font("display"), anchor="w", height=26).pack(fill="x")
    if subtitle:
        ctk.CTkLabel(text, text=subtitle, font=font("body"), text_color=C["text_muted"],
                     anchor="w", justify="left", height=20).pack(fill="x")
    return row


class PathField(ctk.CTkFrame):
    """Label + entry + Browse button on one row. The entry is `field.entry`."""

    def __init__(self, master, label, placeholder="", command=None, label_width=120,
                 button_text="Browse…", **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=label, width=label_width, anchor="w", font=font("body"),
                     text_color=C["text_muted"]).grid(row=0, column=0, sticky="w", padx=(0, GAP))
        self.entry = ctk.CTkEntry(self, placeholder_text=placeholder, height=34, font=font("body"))
        self.entry.grid(row=0, column=1, sticky="ew")
        self.button = Button(self, button_text, kind="secondary", width=92, command=command)
        self.button.grid(row=0, column=2, padx=(8, 0))

    def get(self):
        return self.entry.get()


class ProgressBar(ctk.CTkProgressBar):
    """CTkProgressBar that can drive a "42%" label and doesn't show a dot at 0%."""

    def __init__(self, master, percent_label=None, **kwargs):
        self._percent_label = percent_label
        self._real_progress_color = None
        kwargs.setdefault("height", 8)
        super().__init__(master, **kwargs)
        self._real_progress_color = self._progress_color
        self.set(0)

    def set(self, value, from_variable_callback=False):
        super().set(value, from_variable_callback)
        value = max(0.0, min(1.0, float(value)))
        if self._real_progress_color is not None:
            wanted = self._fg_color if value <= 0 else self._real_progress_color
            if self._progress_color != wanted:
                self.configure(progress_color=wanted)
        if self._percent_label is not None:
            self._percent_label.configure(text="{}%".format(int(round(value * 100))))


class LogBox(ctk.CTkTextbox):
    """Read-only log that accepts the same calls as the tk.Listbox logs it replaces:

        log.insert(tk.END, "Copied: A001_C003.mov")
        log.delete(0, tk.END)

    Known prefixes ("Copied", "Missing", "Error"...) are colored so the log scans at a glance.
    """

    PREFIXES = (
        ("Missing report", "text_subtle"), ("Missing", "warning"), ("Exists, skipped", "text_subtle"),
        ("Skipped", "text_subtle"), ("Copied", "success"), ("Converted", "success"), ("Renamed", "success"),
        ("Moved", "success"), ("Created", "success"), ("Done", "success"), ("Complete", "success"),
        ("Processed", "success"), ("Finished", "success"), ("Successfully", "success"),
        ("Backup completed", "success"), ("Backup failed", "danger"), ("Found", "accent_text"),
        ("Processing", "accent_text"), ("Started", "accent_text"), ("Starting", "accent_text"),
        ("Resumed", "accent_text"), ("Paused", "warning"), ("Warning", "warning"), ("Stop", "warning"),
        ("Error", "danger"), ("Failed", "danger"),
    )
    # "[3/10] Converted: ...", "[14:02:11] Processed: ..." or "[2026-09-23 14:02:11] Starting backup..."
    _COUNTER = re.compile(r"^\[(\d+/\d+|(\d{4}-\d{2}-\d{2} )?\d{1,2}:\d{2}(:\d{2})?)\]\s*")

    def __init__(self, master, empty_text="Nothing here yet.", **kwargs):
        kwargs.setdefault("font", font("mono"))
        kwargs.setdefault("fg_color", C["field"])
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", C["border"])
        kwargs.setdefault("wrap", "none")
        super().__init__(master, **kwargs)
        self._empty_text = empty_text
        self._lines = 0
        self._retint()
        self._show_empty()

    # Listbox-compatible API ------------------------------------------------
    def insert(self, index, text, tags=None):
        if str(index) != tk.END:
            return super().insert(index, text, tags)
        text = str(text).rstrip("\n")               # one entry per insert, even if the caller adds "\n"
        self._textbox.configure(state="normal")
        if self._lines == 0:
            self._textbox.delete("1.0", "end")
        start = self._textbox.index("end-1c")
        self._textbox.insert("end", text + "\n")
        for role, first, last in self._match(text):
            self._textbox.tag_add(role, "{}+{}c".format(start, first), "{}+{}c".format(start, last))
        self._lines += 1
        self._textbox.configure(state="disabled")
        self._textbox.see("end")

    def delete(self, first, last=None):
        if str(first) not in ("0", "1.0") or last is None:
            return super().delete(first, last)
        self._textbox.configure(state="normal")
        self._textbox.delete("1.0", "end")
        self._lines = 0
        self._show_empty()

    def size(self):
        return self._lines

    # internals ---------------------------------------------------------------
    def _match(self, text):
        """Return (role, start, end) spans to color: an optional [n/m] counter, then the keyword."""
        spans = []
        offset = 0
        counter = self._COUNTER.match(text)
        if counter:
            spans.append(("text_subtle", 0, counter.end()))
            offset = counter.end()
        rest = text[offset:]
        for prefix, role in self.PREFIXES:
            if rest.startswith(prefix):
                colon = rest.find(":")
                if 0 <= colon <= len(prefix) + 2:
                    spans.append((role, offset, offset + colon + 1))
                else:                                # a one-line message ("Stop requested.")
                    spans.append((role, offset, len(text)))
                break
        return spans

    def _show_empty(self):
        self._textbox.configure(state="normal")
        self._textbox.insert("end", self._empty_text, "text_subtle")
        self._textbox.configure(state="disabled")

    def _retint(self):
        index = mode_index()
        for role in {role for _, role in self.PREFIXES}:
            self._textbox.tag_configure(role, foreground=C[role][index])

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        self._retint()


class StatusLabel(ctk.CTkLabel):
    """Label that picks its color from its own text, so tool logic can keep calling
    configure(text=...) and the color follows. rules: [("Watching", "success"), ...]"""

    def __init__(self, master, rules=(), default_role="text_muted", **kwargs):
        self._rules = list(rules)
        self._default_role = default_role
        kwargs.setdefault("font", font("body_strong"))
        kwargs.setdefault("anchor", "w")
        super().__init__(master, **kwargs)
        self._recolor(kwargs.get("text", ""))

    def configure(self, require_redraw=False, **kwargs):
        super().configure(require_redraw=require_redraw, **kwargs)
        if "text" in kwargs:
            self._recolor(kwargs["text"])

    def _recolor(self, text):
        role = next((role for needle, role in self._rules if needle in str(text)), self._default_role)
        super().configure(text_color=C[role])


class ThemedListbox(tk.Listbox):
    """tk.Listbox that follows the theme (use where you need selection, not just a log)."""

    def __init__(self, master, text_role="text", **kwargs):
        self._text_role = text_role                 # e.g. "warning" to tint every row
        scale = ctk.ScalingTracker.get_widget_scaling(master)
        kwargs.setdefault("borderwidth", 0)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("activestyle", "none")
        kwargs.setdefault("font", (font("body").cget("family"), -int(round(13 * scale))))
        super().__init__(master, **kwargs)
        self._retint()
        ctk.AppearanceModeTracker.add(self._retint, self)
        self.bind("<Destroy>", self._untrack, add="+")

    def _retint(self, _mode=None):
        index = mode_index()
        self.configure(bg=C["field"][index], fg=C[self._text_role][index],
                       selectbackground=C["accent"][index], selectforeground="#FFFFFF",
                       disabledforeground=C["text_disabled"][index])

    def _untrack(self, event):
        if event.widget is self:
            ctk.AppearanceModeTracker.remove(self._retint)


def add_scrollbar(widget, master, row=0, column=1, orientation="vertical"):
    """Attach a theme-matched CTkScrollbar to a Listbox / Treeview / Text laid out with grid."""
    view = widget.yview if orientation == "vertical" else widget.xview
    bar = ctk.CTkScrollbar(master, orientation=orientation, command=view)
    if orientation == "vertical":
        widget.configure(yscrollcommand=bar.set)
        bar.grid(row=row, column=column, sticky="ns", padx=(4, 0))
    else:
        widget.configure(xscrollcommand=bar.set)
        bar.grid(row=row, column=column, sticky="ew", pady=(4, 0))
    return bar


_ttk_styled = False


def style_ttk(widget):
    """Make ttk.Treeview / Scrollbar / Notebook match the theme (safe to call repeatedly)."""
    global _ttk_styled
    style = ttk.Style(widget)
    scale = ctk.ScalingTracker.get_widget_scaling(widget)
    family = font("body").cget("family")

    def restyle(_mode=None):
        i = mode_index()
        field, surface, surface_2 = C["field"][i], C["surface"][i], C["surface_2"][i]
        thumb, thumb_hover = C["border_strong"][i], C["text_subtle"][i]
        style.theme_use("clam")
        style.configure("Treeview", background=field, fieldbackground=field, foreground=C["text"][i],
                        bordercolor=C["border"][i], lightcolor=field, darkcolor=field, borderwidth=1,
                        rowheight=int(28 * scale), font=(family, -int(13 * scale)))
        style.map("Treeview", background=[("disabled", field), ("selected", C["accent"][i])],
                  foreground=[("disabled", C["text_disabled"][i]), ("selected", "#FFFFFF")])
        style.configure("Treeview.Heading", background=surface_2, foreground=C["text_muted"][i],
                        bordercolor=surface_2, lightcolor=surface_2, darkcolor=surface_2,
                        relief="flat", borderwidth=0, font=(family, -int(12 * scale), "bold"))
        style.map("Treeview.Heading", background=[("active", C["hover"][i])])
        # ttk scrollbars switch to "disabled" whenever everything fits, so map every state
        for orient in ("Vertical", "Horizontal"):
            name = orient + ".TScrollbar"
            style.configure(name, troughcolor=field, background=thumb, bordercolor=field,
                            lightcolor=thumb, darkcolor=thumb, arrowcolor=C["text_muted"][i],
                            relief="flat", gripcount=0)
            style.map(name, background=[("disabled", surface_2), ("pressed", thumb_hover),
                                        ("active", thumb_hover)],
                      lightcolor=[("disabled", surface_2)], darkcolor=[("disabled", surface_2)],
                      arrowcolor=[("disabled", C["text_disabled"][i])])
        style.configure("TNotebook", background=surface, bordercolor=C["border"][i],
                        lightcolor=surface, darkcolor=surface, borderwidth=0)
        style.configure("TNotebook.Tab", background=surface_2, foreground=C["text_muted"][i],
                        bordercolor=C["border"][i], lightcolor=surface_2, darkcolor=surface_2,
                        padding=(14, 6), borderwidth=0, font=(family, -int(13 * scale)))
        style.map("TNotebook.Tab", background=[("selected", surface)],
                  lightcolor=[("selected", surface)], foreground=[("selected", C["text"][i])])

    restyle()
    if not _ttk_styled:
        ctk.AppearanceModeTracker.add(restyle, widget)
        _ttk_styled = True


def make_clickable(frame, on_click, on_hover=None, cursor="hand2"):
    """Make a composite widget act like one button: hover + click anywhere inside it."""
    tag = "clickable{}".format(id(frame))

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            for grandchild in walk(child):
                yield grandchild

    for widget in walk(frame):
        tk.Misc.bindtags(widget, (tag,) + tuple(tk.Misc.bindtags(widget)))
        try:
            tk.Misc.configure(widget, cursor=cursor)
        except tk.TclError:
            pass

    def pointer_inside():
        try:
            widget = frame.winfo_containing(*frame.winfo_pointerxy())
        except (KeyError, tk.TclError):
            return False
        while widget is not None:
            if widget is frame:
                return True
            widget = getattr(widget, "master", None)
        return False

    def enter(_event):
        if on_hover:
            on_hover(True)

    def leave(_event):
        if on_hover and not pointer_inside():
            on_hover(False)

    def release(_event):
        if pointer_inside():
            on_click()

    tk.Misc.bind_class(frame, tag, "<Enter>", enter)
    tk.Misc.bind_class(frame, tag, "<Leave>", leave)
    tk.Misc.bind_class(frame, tag, "<ButtonRelease-1>", release)
