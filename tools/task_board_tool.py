"""Task Board: a to-do list built from your email and Slack mentions, kept on this computer.

All the checking and storage is in taskboard_core.py (next to main.py). This file is the window.
Checks run in the background every few minutes while Auto AE is open; for checks when it's
closed, schedule `python taskboard_core.py --check` with Task Scheduler (Windows) or cron/launchd.
"""
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from tkinter import messagebox

import customtkinter as ctk
from base_tool import BaseTool
import theme
from theme import C, font

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
import taskboard_core as core  # noqa: E402

FILTERS = ["Open", "Email", "Slack", "Teams", "Manual", "Done"]
PRIORITY_LABEL = {"high": "High priority", "normal": "Normal", "low": "Low"}
PILL = {  # (text color, background) per priority, light/dark pairs
    "high": (C["danger"], C["danger_soft"]),
    "normal": (C["warning"], C["surface_2"]),
    "low": (C["text_muted"], C["surface_2"]),
}
SLACK_APP_URL = "https://api.slack.com/apps"
GMAIL_APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"


def open_path(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def friendly_time(iso):
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except Exception:
        return ""
    now = datetime.now().astimezone()
    if when.date() == now.date():
        return when.strftime("%I:%M %p").lstrip("0")
    return when.strftime("%b %d, %I:%M %p").replace(" 0", " ")


def due_label(iso_date):
    left = core.days_until(iso_date)
    if left is None:
        return ""
    if left < 0:
        return "Overdue · {}".format(iso_date[5:].replace("-", "/"))
    if left == 0:
        return "Due today"
    if left == 1:
        return "Due tomorrow"
    return "Due {}".format(datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %d").replace(" 0", " "))


class BackgroundChecker:
    """Runs a check every N minutes on a daemon thread while Auto AE is open."""

    def __init__(self):
        self.store = None
        self.running = False
        self.wake = threading.Event()
        self.lock = threading.Lock()

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._loop, name="TaskBoardChecker", daemon=True).start()

    def check_now(self):
        self.wake.set()

    def _loop(self):
        time.sleep(20)  # let the app finish opening first
        while self.running:
            config = core.load_config()
            if any(config.get(k) for k in ("email_enabled", "slack_enabled", "outlook_enabled", "teams_enabled")):
                try:
                    core.run_check(log=lambda *_: None)
                except Exception as error:  # never let the thread die
                    print("Task Board check failed:", error)
            minutes = max(5, int(config.get("check_every_minutes") or 30))
            self.wake.wait(minutes * 60)
            self.wake.clear()


CHECKER = BackgroundChecker()


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Task Board"
        self.description = "To-do list from your email and Slack mentions, updated automatically"
        self.category = "Editorial & Turnover"
        self.icon = "check"
        self.window = None
        self.filter = "Open"
        self.store = None
        self._last_seen_check = None
        self._busy = False
        CHECKER.start()

    # ------------------------------------------------------------------ window
    def create_window(self, parent):
        self.store = core.TaskStore()
        window = self.create_tool_window(parent, self.name, 860, 760)
        window.minsize(640, 520)
        self.window = window

        page = theme.page(window)
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", pady=(0, 16))
        theme.tool_header(top, self).pack(side="left", fill="x", expand=True)
        theme.Button(top, "Settings", kind="secondary", width=96, command=self.open_settings).pack(side="right")

        # Status + Check now
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 12))
        self.status = theme.StatusLabel(bar, text="", rules=[
            ("Couldn't", "warning"), ("Set up", "warning"), ("Checking", "accent_text")],
            font=font("caption"))
        self.status.pack(side="left", fill="x", expand=True)
        self.check_button = theme.Button(bar, "Check now", kind="primary", width=120,
                                         image=theme.icon("refresh-cw", "#FFFFFF", 15), compound="left",
                                         command=self.check_now)
        self.check_button.pack(side="right")

        # Filters + stats
        filters = ctk.CTkFrame(page, fg_color="transparent")
        filters.pack(fill="x", pady=(0, 10))
        self.filter_bar = ctk.CTkSegmentedButton(filters, values=FILTERS, command=self.set_filter,
                                                 font=font("caption"), height=30)
        self.filter_bar.set(self.filter)
        self.filter_bar.pack(side="left")
        self.counts = theme.muted(filters, "")
        self.counts.pack(side="right")

        # Add a task
        add = ctk.CTkFrame(page, fg_color="transparent")
        add.pack(fill="x", pady=(0, 12))
        self.add_entry = ctk.CTkEntry(add, placeholder_text="Add a task…", height=34, font=font("body"))
        self.add_entry.pack(side="left", fill="x", expand=True)
        self.add_entry.bind("<Return>", lambda e: self.add_task())
        self.add_priority = ctk.CTkOptionMenu(add, values=["Normal", "High", "Low"], width=100, height=34,
                                              font=font("body"))
        self.add_priority.pack(side="left", padx=8)
        theme.Button(add, "Add", kind="secondary", width=70, image=theme.icon("plus", C["text_muted"], 15),
                     compound="left", command=self.add_task).pack(side="left")

        # List
        self.list_frame = ctk.CTkScrollableFrame(page, fg_color=C["field"], corner_radius=theme.RADIUS,
                                                 border_width=1, border_color=C["border"])
        self.list_frame.pack(fill="both", expand=True)
        self.list_frame.grid_columnconfigure(0, weight=1)

        self.render()
        self.refresh_status()
        self._poll()
        cfg = core.load_config()
        if not any(cfg.get(k) for k in ("email_enabled", "slack_enabled", "outlook_enabled", "teams_enabled")):
            window.after(400, self.open_settings)
        return window

    def close_window(self):
        self.window = None

    # ------------------------------------------------------------------ rendering
    def visible_tasks(self):
        tasks = self.store.all()
        if self.filter == "Open":
            return [t for t in tasks if t["status"] == "open"]
        if self.filter == "Done":
            done = [t for t in tasks if t["status"] == "done"]
            return sorted(done, key=lambda t: t.get("done_at") or "", reverse=True)
        source = self.filter.lower()
        return [t for t in tasks if t["source"] == source and t["status"] == "open"]

    def render(self):
        if not self.window or not self.window.winfo_exists():
            return
        for child in self.list_frame.winfo_children():
            child.destroy()
        tasks = self.visible_tasks()
        all_tasks = self.store.all()
        open_count = sum(1 for t in all_tasks if t["status"] == "open")
        high = sum(1 for t in all_tasks if t["status"] == "open" and t["priority"] == "high")
        self.counts.configure(text="{} open · {} high priority".format(open_count, high))

        if not tasks:
            message = {"Open": "Nothing open. Nice.", "Done": "Nothing finished yet."}.get(
                self.filter, "No open {} tasks.".format(self.filter.lower()))
            theme.muted(self.list_frame, message, role="body", anchor="center").grid(row=0, column=0, pady=40)
            return

        row, group = 0, None
        for task in tasks:
            label = "Done" if task["status"] == "done" else PRIORITY_LABEL.get(task["priority"], "Normal")
            if label != group:
                group = label
                count = sum(1 for t in tasks if ("Done" if t["status"] == "done" else
                                                 PRIORITY_LABEL.get(t["priority"], "Normal")) == label)
                theme.overline(self.list_frame, "{} · {}".format(label, count)).grid(
                    row=row, column=0, sticky="w", padx=14, pady=(14 if row else 10, 4))
                row += 1
            TaskRow(self.list_frame, task, self).grid(row=row, column=0, sticky="ew", padx=8, pady=3)
            row += 1

    def refresh_status(self):
        if not self.window or not self.window.winfo_exists():
            return
        config = core.load_config()
        last = self.store.get_meta("last_check") or {}
        any_on = any(config.get(k) for k in ("email_enabled", "slack_enabled", "outlook_enabled", "teams_enabled"))
        if self._busy:
            text = "Checking…"
        elif not any_on:
            text = "Set up a source in Settings to start"
        elif last.get("errors"):
            text = "Couldn't finish the last check: " + last["errors"][0]
        elif last.get("at"):
            sources = ", ".join(s for s, on in (("email", config.get("email_enabled")),
                                                ("Slack", config.get("slack_enabled")),
                                                ("Outlook", config.get("outlook_enabled")),
                                                ("Teams", config.get("teams_enabled"))) if on)
            text = "Checked {} at {} · checks every {} min while Auto AE is open".format(
                sources, friendly_time(last["at"]), config.get("check_every_minutes", 30))
        else:
            text = "Not checked yet. The first check starts shortly, or click Check now."
        self.status.configure(text=theme_wrap(text, 90))

    def _poll(self):
        """Pick up background checks (and edits from another window) every few seconds."""
        if not self.window or not self.window.winfo_exists():
            return
        last = (self.store.get_meta("last_check") or {}).get("at")
        if last != self._last_seen_check:
            self._last_seen_check = last
            self.render()
            self.refresh_status()
        self.window.after(4000, self._poll)

    # ------------------------------------------------------------------ actions
    def set_filter(self, value):
        self.filter = value
        self.render()

    def check_now(self):
        if self._busy:
            return
        self._busy = True
        self.check_button.configure(state="disabled")
        self.refresh_status()

        def work():
            result = core.run_check(log=lambda *_: None)
            if self.window and self.window.winfo_exists():
                self.window.after(0, lambda: self._check_finished(result))

        threading.Thread(target=work, daemon=True).start()

    def _check_finished(self, result):
        self._busy = False
        if not self.window or not self.window.winfo_exists():
            return
        self.check_button.configure(state="normal")
        self._last_seen_check = None  # force a redraw
        self._poll_once()
        if result.get("errors") and not result.get("skipped"):
            messagebox.showwarning("Check finished with problems", "\n\n".join(result["errors"]), parent=self.window)

    def _poll_once(self):
        self._last_seen_check = (self.store.get_meta("last_check") or {}).get("at")
        self.render()
        self.refresh_status()

    def add_task(self):
        title = self.add_entry.get().strip()
        if not title:
            return
        self.store.add_manual(title, priority=self.add_priority.get().lower())
        self.add_entry.delete(0, "end")
        self.render()

    def toggle(self, task, done):
        self.store.set_done(task["id"], done)
        # Leave the row visible for a moment so a mis-click can be undone
        self.window.after(700, self.render)

    def remove(self, task):
        if task["source"] != "manual" and not messagebox.askyesno(
                "Remove task", "Remove “{}”?\n\nIt won't come back from the same email or message.".format(
                    task["title"]), parent=self.window):
            return
        if task["source"] == "manual":
            self.store.delete(task["id"])
        else:
            self.store.set_done(task["id"], True, by="removed")  # keeps the id so it isn't re-added
        self.render()

    def open_settings(self):
        SettingsDialog(self.window, on_saved=self._settings_saved)

    def _settings_saved(self):
        self.refresh_status()
        self.check_now()


def theme_wrap(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


class TaskRow(ctk.CTkFrame):
    def __init__(self, master, task, tool):
        ctk.CTkFrame.__init__(self, master, fg_color=C["surface"], corner_radius=10, border_width=1,
                              border_color=C["border"])
        self.grid_columnconfigure(1, weight=1)
        done = task["status"] == "done"
        var = ctk.BooleanVar(value=done)
        box = ctk.CTkCheckBox(self, text="", variable=var, width=22, checkbox_width=20, checkbox_height=20,
                              command=lambda: tool.toggle(task, var.get()))
        box.grid(row=0, column=0, rowspan=3, sticky="n", padx=(12, 4), pady=(12, 0))

        title = ctk.CTkLabel(self, text=task["title"], font=font("body_strong"), anchor="w", justify="left",
                             text_color=C["text_disabled"] if done else C["text"], wraplength=560)
        title.grid(row=0, column=1, sticky="ew", pady=(10, 0))
        note = task["note"] if not done else (task.get("done_by") and "Done · {}".format(task["done_by"])) or ""
        if note:
            theme.muted(self, note, wraplength=560).grid(row=1, column=1, sticky="ew", pady=(1, 0))

        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=2, column=1, sticky="w", pady=(6, 10))
        if not done:
            fg, bg = PILL.get(task["priority"], PILL["normal"])
            ctk.CTkLabel(meta, text=task["priority"].capitalize(), font=font("caption"), text_color=fg,
                         fg_color=bg, corner_radius=6, height=22, width=10).pack(side="left", padx=(0, 6))
        if task["due"] and not done:
            ctk.CTkLabel(meta, text=due_label(task["due"]), font=font("caption"), text_color=C["accent_text"],
                         fg_color=C["accent_soft"], corner_radius=6, height=22, width=10).pack(side="left", padx=(0, 6))
        bits = [b for b in (task.get("sender"), task["category"] if task["source"] not in ("slack", "teams") else "") if b]
        if bits:
            theme.muted(meta, " · ".join(bits), height=22).pack(side="left", padx=(2, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=0, column=2, rowspan=3, sticky="ne", padx=(8, 10), pady=8)
        if task["url"]:
            label = {"email": "Open email", "slack": "Open in Slack", "teams": "Open in Teams"}.get(
                task["source"], "Open")
            theme.Button(actions, label, kind="secondary", width=110, height=28, font=font("caption"),
                         image=theme.icon("external-link", C["text_muted"], 13), compound="right",
                         command=lambda: webbrowser.open(task["url"])).pack(side="left")
        theme.Button(actions, "", kind="ghost", width=28, height=28,
                     image=theme.icon("x", C["text_subtle"], 14),
                     command=lambda: tool.remove(task)).pack(side="left", padx=(4, 0))


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_saved=None):
        ctk.CTkToplevel.__init__(self, parent)
        self.title("Task Board settings")
        theme.center(self, 640, 780)
        self.minsize(560, 560)
        self.transient(parent)
        theme.set_app_icon(self)
        self.on_saved = on_saved
        self.config_data = core.load_config()

        actions = theme.action_bar(self, pady=(0, 16))
        actions.pack_configure(padx=24)
        theme.Button(actions, "Cancel", kind="ghost", width=80, command=self.destroy).pack(side="left")
        theme.Button(actions, "Save", kind="primary", width=110, command=self.save).pack(side="right")

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(16, 8))

        # --- Email
        card = theme.Card(body, title="Email", subtitle="Checks your inbox over IMAP with an app password.")
        card.pack(fill="x", padx=8, pady=(0, 12))
        b = card.body
        self.email_on = ctk.CTkSwitch(b, text="Check my email", font=font("body"))
        self.email_on.pack(anchor="w", pady=(0, 10))
        if self.config_data.get("email_enabled"):
            self.email_on.select()
        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x")
        self.provider = ctk.CTkOptionMenu(grid, values=list(core.IMAP_PRESETS), command=self.pick_provider,
                                          font=font("body"), height=32)
        theme.form_row(grid, 0, "Provider", self.provider)
        self.address = self._entry(grid, 1, "Email address", self.config_data.get("email_address"))
        self.name = self._entry(grid, 2, "Your first name", self.config_data.get("your_name"),
                                "What people call you in emails")
        self.password = self._entry(grid, 3, "App password", "", "Leave blank to keep the saved one", show="•")
        self.host = self._entry(grid, 4, "IMAP server", self.config_data.get("imap_host"))
        self.folder = self._entry(grid, 5, "Folder", self.config_data.get("imap_folder") or "INBOX")
        preset = next((k for k, (h, _) in core.IMAP_PRESETS.items() if h and h == self.config_data.get("imap_host")),
                      "Other")
        self.provider.set(preset)
        saved = "saved" if core.get_secret("imap_password") else "not saved yet"
        theme.muted(b, "App password is {} (kept in {}). Gmail: turn on 2-Step Verification, then create "
                       "one under App passwords.".format(saved, core.secure_storage_name()),
                    wraplength=540).pack(fill="x", pady=(2, 6))
        links = ctk.CTkFrame(b, fg_color="transparent")
        links.pack(fill="x")
        theme.Button(links, "Create a Gmail app password", kind="ghost", height=28, font=font("caption"),
                     command=lambda: webbrowser.open(GMAIL_APP_PASSWORD_URL)).pack(side="left")
        theme.Button(links, "Test email", kind="secondary", width=100, height=30,
                     command=self.test_email).pack(side="right")

        # --- Slack
        card = theme.Card(body, title="Slack", subtitle="Adds messages that @mention you and ask for something.")
        card.pack(fill="x", padx=8, pady=(0, 12))
        b = card.body
        self.slack_on = ctk.CTkSwitch(b, text="Check my Slack mentions", font=font("body"))
        self.slack_on.pack(anchor="w", pady=(0, 10))
        if self.config_data.get("slack_enabled"):
            self.slack_on.select()
        grid = ctk.CTkFrame(b, fg_color="transparent")
        grid.pack(fill="x")
        saved = "saved" if core.get_secret("slack_token") else "not saved yet"
        self.token = self._entry(grid, 0, "User token", "", "xoxp-…  (blank keeps the saved one)", show="•")
        theme.muted(b, "Token is {}. To get one: create a Slack app at api.slack.com/apps, add the User Token "
                       "Scopes search:read, channels:history, groups:history, im:history, mpim:history and "
                       "users:read, install it to your workspace, and copy the User OAuth Token."
                       .format(saved), wraplength=540).pack(fill="x", pady=(2, 6))
        links = ctk.CTkFrame(b, fg_color="transparent")
        links.pack(fill="x")
        theme.Button(links, "Open Slack apps page", kind="ghost", height=28, font=font("caption"),
                     command=lambda: webbrowser.open(SLACK_APP_URL)).pack(side="left")
        theme.Button(links, "Test Slack", kind="secondary", width=100, height=30,
                     command=self.test_slack).pack(side="right")

        # --- Microsoft (Outlook + Teams)
        card = theme.Card(body, title="Outlook & Teams",
                          subtitle="One Microsoft sign-in covers both your Outlook mail and Teams @mentions.")
        card.pack(fill="x", padx=8, pady=(0, 12))
        b = card.body
        self.outlook_on = ctk.CTkSwitch(b, text="Check my Outlook mail", font=font("body"))
        self.outlook_on.pack(anchor="w", pady=(0, 6))
        if self.config_data.get("outlook_enabled"):
            self.outlook_on.select()
        self.teams_on = ctk.CTkSwitch(b, text="Check my Teams @mentions", font=font("body"))
        self.teams_on.pack(anchor="w", pady=(0, 10))
        if self.config_data.get("teams_enabled"):
            self.teams_on.select()

        connect_row = ctk.CTkFrame(b, fg_color="transparent")
        connect_row.pack(fill="x", pady=(0, 4))
        account = self.config_data.get("ms_account", "")
        connected = bool(core.get_secret("ms_refresh_token"))
        self.ms_status = theme.StatusLabel(connect_row, text=(
            "Connected as {}".format(account) if connected and account else
            "Connected" if connected else "Not connected"),
            rules=[("Connected", "success"), ("Not connected", "warning")])
        self.ms_status.pack(side="left")
        self.ms_connect_button = theme.Button(connect_row, "Connect Microsoft account", kind="secondary",
                                              width=190, command=self.connect_microsoft)
        self.ms_connect_button.pack(side="right")
        theme.Button(connect_row, "Disconnect", kind="ghost", width=90,
                     command=self.disconnect_microsoft).pack(side="right", padx=(0, 8))

        theme.muted(b, "Signs you in with Microsoft's device-code flow: click Connect, then finish it in your "
                       "browser. Needs the Mail.Read, Chat.Read and ChannelMessage.Read.All permissions - in "
                       "some organizations, a Teams admin has to approve ChannelMessage.Read.All the first "
                       "time anyone signs in with it.", wraplength=540).pack(fill="x", pady=(6, 8))

        adv = ctk.CTkFrame(b, fg_color="transparent")
        adv.pack(fill="x")
        self.ms_client_id = self._entry(adv, 0, "Client ID (advanced)", self.config_data.get("ms_client_id", ""),
                                        "Leave blank to use Auto AE's built-in app")
        self.ms_tenant = self._entry(adv, 1, "Tenant (advanced)", self.config_data.get("ms_tenant", "common"),
                                     "\"common\", or your organization's tenant id")
        theme.muted(b, "Only change these if your organization blocks the default sign-in app, or gives you "
                       "your own Azure AD app to use instead.", wraplength=540).pack(fill="x", pady=(2, 0))

        # --- General
        card = theme.Card(body, title="Checking")
        card.pack(fill="x", padx=8, pady=(0, 12))
        grid = ctk.CTkFrame(card.body, fg_color="transparent")
        grid.pack(fill="x")
        self.every = ctk.CTkOptionMenu(grid, values=["5", "10", "15", "30", "60"], font=font("body"), height=32)
        self.every.set(str(self.config_data.get("check_every_minutes", 30)))
        theme.form_row(grid, 0, "Every (minutes)", self.every, sticky="w")
        self.days = ctk.CTkOptionMenu(grid, values=["3", "7", "14", "30"], font=font("body"), height=32)
        self.days.set(str(self.config_data.get("first_check_days", 14)))
        theme.form_row(grid, 1, "First check looks back (days)", self.days, label_width=200, sticky="w")
        theme.muted(card.body, "What counts as a task is set in taskboard_rules.json: senders to always or never "
                               "include, words that mean a request, and what a finished Slack reply sounds like.",
                    wraplength=540).pack(fill="x", pady=(4, 8))
        row = ctk.CTkFrame(card.body, fg_color="transparent")
        row.pack(fill="x")
        theme.Button(row, "Edit rules", kind="secondary", width=110, command=self.edit_rules).pack(side="left")
        theme.Button(row, "Open data folder", kind="ghost", width=130,
                     command=lambda: open_path(core.data_folder())).pack(side="left", padx=(8, 0))

    def _entry(self, grid, row, label, value="", placeholder="", show=None):
        entry = ctk.CTkEntry(grid, height=32, font=font("body"), placeholder_text=placeholder, show=show)
        if value:
            entry.insert(0, str(value))
        theme.form_row(grid, row, label, entry, label_width=130)
        return entry

    def pick_provider(self, name):
        host, port = core.IMAP_PRESETS.get(name, ("", 993))
        if host:
            self.host.delete(0, "end")
            self.host.insert(0, host)

    def collect(self):
        config = dict(self.config_data)
        config.update({
            "email_enabled": bool(self.email_on.get()),
            "email_address": self.address.get().strip(),
            "your_name": self.name.get().strip(),
            "imap_host": self.host.get().strip(),
            "imap_folder": self.folder.get().strip() or "INBOX",
            "slack_enabled": bool(self.slack_on.get()),
            "outlook_enabled": bool(self.outlook_on.get()),
            "teams_enabled": bool(self.teams_on.get()),
            "ms_client_id": self.ms_client_id.get().strip(),
            "ms_tenant": self.ms_tenant.get().strip() or "common",
            "check_every_minutes": int(self.every.get()),
            "first_check_days": int(self.days.get()),
        })
        return config

    def store_secrets(self):
        if self.password.get().strip():
            core.set_secret("imap_password", self.password.get().replace(" ", "").strip())
        if self.token.get().strip():
            core.set_secret("slack_token", self.token.get().strip())

    def save(self):
        config = self.collect()
        if config["email_enabled"] and not (config["email_address"] and config["imap_host"]):
            messagebox.showwarning("Email needs details", "Add your email address and IMAP server, or turn email off.",
                                   parent=self)
            return
        if (config["outlook_enabled"] or config["teams_enabled"]) and not core.get_secret("ms_refresh_token"):
            messagebox.showwarning("Microsoft account needed",
                                   "Click Connect Microsoft account, or turn Outlook and Teams off.", parent=self)
            return
        self.store_secrets()
        try:
            core.save_config(config)
        except OSError as error:
            messagebox.showerror("Couldn't save settings", str(error), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()

    def _test(self, label, make_checker):
        self.store_secrets()
        config = self.collect()
        try:
            make_checker(config)
            messagebox.showinfo(label, "Connected. {} is working.".format(label.replace("Test ", "")), parent=self)
        except core.CheckError as error:
            messagebox.showwarning(label, str(error), parent=self)
        except Exception as error:
            messagebox.showwarning(label, "{}: {}".format(type(error).__name__, error), parent=self)

    def test_email(self):
        def run(config):
            imap = core.EmailChecker(config, None, core.DEFAULT_RULES).connect()
            imap.logout()
        self._test("Test email", run)

    def test_slack(self):
        def run(config):
            token = core.get_secret("slack_token")
            if not token:
                raise core.CheckError("Paste your Slack user token first.")
            core.SlackAPI(token).call("auth.test")
            core.SlackAPI(token).call("search.messages", query="test", count=1)
        self._test("Test Slack", run)

    def connect_microsoft(self):
        client_id = self.ms_client_id.get().strip()
        tenant = self.ms_tenant.get().strip() or "common"
        auth = core.GraphAuth(client_id, tenant)
        try:
            device = auth.start_device_code()
        except core.CheckError as error:
            messagebox.showwarning("Couldn't start sign-in", str(error), parent=self)
            return
        MicrosoftConnectDialog(self, auth, device, on_connected=self._microsoft_connected)

    def _microsoft_connected(self, account):
        if self.winfo_exists():
            self.config_data["ms_account"] = account or self.config_data.get("ms_account", "")
            self.ms_status.configure(text="Connected as {}".format(account) if account else "Connected")

    def disconnect_microsoft(self):
        if not core.get_secret("ms_refresh_token"):
            return
        if not messagebox.askyesno("Disconnect Microsoft account",
                                   "Outlook and Teams checking will stop until you sign in again.", parent=self):
            return
        core.GraphAuth().disconnect()
        self.ms_status.configure(text="Not connected")

    def edit_rules(self):
        try:
            core.load_rules()  # writes the default file the first time
        except core.RulesError as error:
            messagebox.showwarning("Rules file", str(error), parent=self)
        open_path(core.rules_path())


class MicrosoftConnectDialog(ctk.CTkToplevel):
    """Shows the Microsoft device-code sign-in (a code to enter at microsoft.com/devicelogin) and
    polls in the background until the person finishes, or cancels by closing the window."""

    def __init__(self, parent, auth, device, on_connected=None):
        ctk.CTkToplevel.__init__(self, parent)
        self.title("Sign in to Microsoft")
        theme.center(self, 420, 300)
        self.resizable(False, False)
        self.transient(parent)
        theme.set_app_icon(self)
        self.auth = auth
        self.device = device
        self.on_connected = on_connected
        self._done = False
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        pad = ctk.CTkFrame(self, fg_color="transparent")
        pad.pack(fill="both", expand=True, padx=24, pady=20)
        theme.muted(pad, "1.  Go to this page:", role="body").pack(anchor="w")
        theme.Button(pad, device.get("verification_uri", ""), kind="secondary", height=32,
                     command=lambda: webbrowser.open(device.get("verification_uri", ""))).pack(fill="x", pady=(4, 14))
        theme.muted(pad, "2.  Enter this code:", role="body").pack(anchor="w")
        code = ctk.CTkLabel(pad, text=device.get("user_code", ""), font=font("h2"))
        code.pack(pady=(4, 4))
        theme.Button(pad, "Copy code", kind="ghost", width=110, height=28,
                     command=self._copy_code).pack(pady=(0, 14))
        self.status = theme.StatusLabel(pad, text="Waiting for you to finish signing in…",
                                        rules=[("failed", "warning"), ("expired", "warning")])
        self.status.pack(fill="x")
        threading.Thread(target=self._poll, daemon=True).start()

    def _copy_code(self):
        self.clipboard_clear()
        self.clipboard_append(self.device.get("user_code", ""))

    def _poll(self):
        try:
            self.auth.poll_device_code(self.device.get("device_code"), self.device.get("interval", 5),
                                       self.device.get("expires_in", 900))
        except core.CheckError as error:
            if self.winfo_exists():
                self.after(0, lambda: self._failed(str(error)))
            return
        account = ""
        try:
            info = core.GraphAPI(self.auth).get("/me?$select=mail,userPrincipalName")
            account = info.get("mail") or info.get("userPrincipalName") or ""
        except core.CheckError:
            pass
        if self.winfo_exists():
            self.after(0, lambda: self._succeeded(account))

    def _succeeded(self, account):
        self._done = True
        if self.on_connected:
            self.on_connected(account)
        self.destroy()

    def _failed(self, message):
        self._done = True
        self.status.configure(text=message)

    def _cancel(self):
        self.destroy()  # the polling thread just finishes quietly once the dialog is gone
