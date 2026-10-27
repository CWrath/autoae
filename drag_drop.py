"""Shared drag-and-drop helper for tools that take a path in an entry field."""

import tkinter as tk

from tkinterdnd2 import COPY, DND_FILES, TkinterDnD


def enable_path_drop(window, entry, on_drop, *extra_targets):
    """Let files/folders dropped on `entry` (or extra widgets) call on_drop(paths).

    Drag and drop is optional: if TkDND can't load, the Browse buttons still work.
    """
    def handle(event):
        paths = list(window.tk.splitlist(event.data))
        if paths:
            on_drop(paths)
        return COPY

    try:
        TkinterDnD._require(window)
        # CTkEntry wraps a real tk.Entry in `_entry`; register both layers.
        for widget in (entry, getattr(entry, "_entry", None), *extra_targets):
            if widget is not None:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", handle)
    except (RuntimeError, tk.TclError, AttributeError):
        return False
    return True
