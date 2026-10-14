"""Task Board: turns email and Slack into a to-do list, all on this computer (no AI service).

    Email    IMAP with an app password (Gmail, Google Workspace, iCloud, Fastmail...).
    Slack    a Slack *user* token (xoxp-...) with the search:read, channels:history, groups:history,
             im:history and mpim:history scopes.
    Outlook  Outlook/Microsoft 365 mail, via Microsoft Graph (a device-code sign-in, not IMAP -
             Microsoft has been turning off basic-auth IMAP access for most accounts).
    Teams    Microsoft Teams @mentions, via the same Microsoft Graph sign-in as Outlook.
    Rules    taskboard_rules.json in the data folder decides what counts as a task. Edit it to tune.
    Tasks    taskboard.db (SQLite) in the data folder.

The Auto AE tool (tools/task_board_tool.py) is the window for all of this. The same checks can
also run with no window, e.g. from Windows Task Scheduler or cron:

    python taskboard_core.py --check        check email + Slack once
    python taskboard_core.py --list         print open tasks

Python 3.9+, standard library only (keyring is used for passwords when it's installed).
"""
import copy
import email
import email.header
import email.utils
import html
import imaplib
import json
import os
import re
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

APP_KEYRING = "AutoAE Task Board"

# ----------------------------------------------------------------------------- locations


def data_folder():
    """One folder per computer user, so each person on a shared machine has their own board."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        path = os.path.join(base, "AutoAE", "task_board")
    elif sys.platform == "darwin":
        path = os.path.expanduser("~/Library/Application Support/AUTO_AE/task_board")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        path = os.path.join(base, "autoae", "task_board")
    os.makedirs(path, exist_ok=True)
    return path


def config_path():
    return os.path.join(data_folder(), "taskboard_config.json")


def rules_path():
    return os.path.join(data_folder(), "taskboard_rules.json")


def db_path():
    return os.path.join(data_folder(), "taskboard.db")


# ----------------------------------------------------------------------------- settings

IMAP_PRESETS = {
    "Gmail / Google Workspace": ("imap.gmail.com", 993),
    "Outlook / Microsoft 365": ("outlook.office365.com", 993),
    "iCloud": ("imap.mail.me.com", 993),
    "Yahoo": ("imap.mail.yahoo.com", 993),
    "Fastmail": ("imap.fastmail.com", 993),
    "Other": ("", 993),
}

DEFAULT_CONFIG = {
    "email_enabled": False,
    "your_name": "",               # first name people use for you, e.g. "Cody"
    "email_address": "",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "imap_folder": "INBOX",
    "slack_enabled": False,
    "slack_workspace_url": "",      # e.g. https://myteam.slack.com (only used for links)
    "outlook_enabled": False,       # Outlook/Microsoft 365 mail, via Microsoft Graph (not IMAP)
    "teams_enabled": False,         # Microsoft Teams @mentions, via Microsoft Graph
    "ms_client_id": "",             # blank = use the built-in Auto AE app id
    "ms_tenant": "common",          # "common" for personal + work/school, or an org tenant id
    "ms_account": "",               # filled in automatically once you sign in
    "check_every_minutes": 30,      # while Auto AE is open
    "first_check_days": 14,         # how far back the very first check looks
}


def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        with open(config_path(), "r", encoding="utf-8") as handle:
            config.update(json.load(handle))
    except (OSError, ValueError):
        pass
    return config


def save_config(config):
    clean = {k: v for k, v in config.items() if k in DEFAULT_CONFIG or k.startswith("_")}
    tmp = config_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(clean, handle, indent=2)
    os.replace(tmp, config_path())


# Secrets: the OS keychain (Windows Credential Manager / macOS Keychain) when keyring is
# installed, otherwise a separate file readable only by this user.
def _secrets_file():
    return os.path.join(data_folder(), "taskboard_secrets.json")


def _keyring():
    try:
        import keyring  # noqa
        keyring.get_keyring()
        return keyring
    except Exception:
        return None


def secure_storage_name():
    return "your system keychain" if _keyring() else "a private file in the Task Board data folder"


def get_secret(name):
    kr = _keyring()
    if kr:
        try:
            value = kr.get_password(APP_KEYRING, name)
            if value:
                return value
        except Exception:
            pass
    try:
        with open(_secrets_file(), "r", encoding="utf-8") as handle:
            return json.load(handle).get(name, "")
    except (OSError, ValueError):
        return ""


def set_secret(name, value):
    kr = _keyring()
    if kr:
        try:
            if value:
                kr.set_password(APP_KEYRING, name, value)
            else:
                try:
                    kr.delete_password(APP_KEYRING, name)
                except Exception:
                    pass
            _write_secret_file(name, None)
            return
        except Exception:
            pass
    _write_secret_file(name, value or None)


def _write_secret_file(name, value):
    try:
        with open(_secrets_file(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    if not data:
        try:
            os.remove(_secrets_file())
        except OSError:
            pass
        return
    fd = os.open(_secrets_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


# ----------------------------------------------------------------------------- rules

DEFAULT_RULES = {
    "_about": "What counts as a task. Words are matched case-insensitively. Edit, save, and the next "
              "check uses them. Delete this file to get the defaults back.",
    "email": {
        "always_task_from": [],
        "never_task_from": ["no-reply@accounts.google.com"],
        "skip_senders_containing": ["newsletter", "marketing", "promo", "offers@", "deals@", "news@",
                                    "digest", "notifications@linkedin", "robot@", "noreply@steampowered"],
        "skip_subjects_containing": ["% off", "sale", "webinar", "unsubscribe", "your order", "order confirmation",
                                     "has shipped", "receipt", "daily summary", "payment is scheduled",
                                     "payment scheduled", "automatic payment is scheduled", "security alert",
                                     "weekly progress", "digest"],
        "request_phrases": ["can you", "could you", "would you", "can we", "please", "let me know",
                            "need you", "do you have", "are you able", "any update", "checking in",
                            "just checking", "following up", "when you get a chance", "send me", "send over",
                            "waiting on", "reminder", "action required", "please review", "approve"],
        "alert_rules": [
            {"match": ["running out of", "capacity", "disk full", "volume degraded", "drive failed",
                       "backup failed", "failed backup", "smart error", "raid degraded"],
             "category": "Tech", "priority": "normal", "title": "Check alert: {subject}"},
            {"match": ["recall"], "category": "Vehicle", "priority": "normal",
             "title": "Schedule recall service: {subject}"},
            {"match": ["statement is ready", "statement is now available", "minimum due", "amount due",
                       "payment due", "invoice", "bill is ready", "past due"],
             "category": "Money", "priority": "normal", "title": "Pay {sender}"},
            {"match": ["estimate", "proposal", "bid is attached", "work is complete",
                       "completed the work", "mail a check"],
             "category": "Home", "priority": "normal", "title": "Follow up with {sender}: {subject}"},
            {"match": ["mentioned you", "@{me}", "assigned you", "replied to a comment", "added a comment"],
             "category": "Work", "priority": "normal", "title": "Check comment: {subject}"},
        ],
        "urgent_words": ["urgent", "asap", "today", "eod", "end of day", "by tomorrow", "right away",
                         "time sensitive", "overdue", "past due", "final notice"],
    },
    "slack": {
        "skip_if_only": ["thanks", "thank you", "ty", "nice", "great", "awesome", "perfect", "love it", "lol"],
        "request_phrases": ["can you", "could you", "would you", "please", "can we", "do you have",
                            "is this", "are you", "let me know", "lmk", "need", "when you",
                            "export", "ingest", "add ", "make ", "send", "put ", "mark ", "bring in",
                            "transcribe", "upload", "re-", "update", "check", "fix", "ready to be", "is ready",
                            "ready for you", "your way", "coming your way"],
        "done_phrases": ["done", "added", "has been added", "have been added", "ingested", "has been ingested",
                         "uploaded", "exported", "can be found", "found here", "is in ", "are in ",
                         "it's in", "it’s in", "they're in", "fixed", "updated", "sent", "posted", "that's the latest",
                         "that’s the latest", "is the latest", "yep", "yes", "all set", "complete",
                         "finished", "re-enabled", "enabled", "should be good", "f.io/", "http"],
        "ack_only_phrases": ["can do", "will do", "on it", "on it!", "sure", "sure thing", "will look",
                             "looking now", "one sec", "ok", "okay", "copy", "got it", "yes!", "👍"],
        "urgent_words": ["asap", "urgent", "right now", "today", "eod", "before", "waiting"],
    },
    "teams": {
        "skip_if_only": ["thanks", "thank you", "ty", "nice", "great", "awesome", "perfect", "love it", "lol"],
        "request_phrases": ["can you", "could you", "would you", "please", "can we", "do you have",
                            "is this", "are you", "let me know", "need", "when you",
                            "export", "ingest", "add ", "make ", "send", "put ", "mark ", "bring in",
                            "transcribe", "upload", "re-", "update", "check", "fix", "ready to be", "is ready",
                            "ready for you", "your way", "coming your way", "review", "approve"],
        "done_phrases": ["done", "added", "has been added", "have been added", "ingested", "has been ingested",
                         "uploaded", "exported", "can be found", "found here", "is in ", "are in ",
                         "it's in", "it’s in", "they're in", "fixed", "updated", "sent", "posted", "that's the latest",
                         "that’s the latest", "is the latest", "yep", "yes", "all set", "complete",
                         "finished", "re-enabled", "enabled", "should be good", "sharepoint.com", "http"],
        "ack_only_phrases": ["can do", "will do", "on it", "on it!", "sure", "sure thing", "will look",
                             "looking now", "one sec", "ok", "okay", "copy", "got it", "yes!", "👍"],
        "urgent_words": ["asap", "urgent", "right now", "today", "eod", "before", "waiting"],
    },
}


def load_rules():
    rules = copy.deepcopy(DEFAULT_RULES)
    path = rules_path()
    if not os.path.isfile(path):
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(DEFAULT_RULES, handle, indent=2, ensure_ascii=False)
        except OSError:
            pass
        return rules
    try:
        with open(path, "r", encoding="utf-8") as handle:
            user = json.load(handle)
    except (OSError, ValueError) as error:
        raise RulesError("taskboard_rules.json has a mistake in it ({}). Fix it or delete the file "
                         "to get the defaults back.".format(error))
    for section in ("email", "slack", "teams"):
        if isinstance(user.get(section), dict):
            rules[section].update(user[section])
    return rules


class RulesError(Exception):
    pass


class CheckError(Exception):
    """A problem the user can fix (wrong password, bad token...). The message says how."""


# ----------------------------------------------------------------------------- storage

PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


class TaskStore:
    """SQLite store. Safe to use from the UI thread and the checker thread (one connection each)."""

    def __init__(self, path=None):
        self.path = path or db_path()
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, note TEXT DEFAULT '',
                    sender TEXT DEFAULT '', category TEXT DEFAULT 'Work', priority TEXT DEFAULT 'normal',
                    due TEXT DEFAULT '', status TEXT DEFAULT 'open', url TEXT DEFAULT '',
                    created TEXT, updated TEXT, done_at TEXT, done_by TEXT DEFAULT '',
                    source_time TEXT DEFAULT '', extra TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """)

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # meta
    def get_meta(self, key, default=None):
        row = self._conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_meta(self, key, value):
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, json.dumps(value)))

    # tasks
    def get(self, task_id):
        row = self._conn().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row(row) if row else None

    def exists(self, task_id):
        return self._conn().execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is not None

    def all(self):
        rows = self._conn().execute("SELECT * FROM tasks").fetchall()
        return sorted((_row(r) for r in rows), key=sort_key)

    def open_tasks(self, source=None):
        sql, args = "SELECT * FROM tasks WHERE status='open'", []
        if source:
            sql += " AND source=?"
            args.append(source)
        return sorted((_row(r) for r in self._conn().execute(sql, args).fetchall()), key=sort_key)

    def add(self, task):
        now = now_iso()
        task = dict(task)
        task.setdefault("created", now)
        task["updated"] = now
        task.setdefault("status", "open")
        extra = task.pop("extra", {}) or {}
        cols = ["id", "source", "title", "note", "sender", "category", "priority", "due", "status", "url",
                "created", "updated", "done_at", "done_by", "source_time"]
        values = [task.get(c, "" if c not in ("done_at",) else None) for c in cols] + [json.dumps(extra)]
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO tasks({}, extra) VALUES({})".format(
                ", ".join(cols), ", ".join("?" * (len(cols) + 1))), values)

    def update(self, task_id, **fields):
        if not fields:
            return
        if "extra" in fields:
            fields["extra"] = json.dumps(fields["extra"] or {})
        fields["updated"] = now_iso()
        sets = ", ".join("{}=?".format(k) for k in fields)
        with self._conn() as conn:
            conn.execute("UPDATE tasks SET {} WHERE id=?".format(sets), list(fields.values()) + [task_id])

    def set_done(self, task_id, done, by="you", when=None):
        if done:
            self.update(task_id, status="done", done_at=when or now_iso(), done_by=by)
        else:
            self.update(task_id, status="open", done_at=None, done_by="")

    def delete(self, task_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def add_manual(self, title, priority="normal", due=""):
        task_id = "m-{}".format(int(time.time() * 1000))
        self.add({"id": task_id, "source": "manual", "title": title.strip(), "category": "Manual",
                  "priority": priority, "due": due})
        return task_id

    def clear_done(self, older_than_days=30):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self._conn() as conn:
            conn.execute("DELETE FROM tasks WHERE status='done' AND done_at < ?", (cutoff,))


def _row(row):
    task = dict(row)
    try:
        task["extra"] = json.loads(task.get("extra") or "{}")
    except ValueError:
        task["extra"] = {}
    return task


def sort_key(task):
    return (task["status"] == "done", PRIORITY_ORDER.get(task["priority"], 1), task["due"] or "9999",
            -(_ts(task.get("source_time") or task.get("created"))))


def _ts(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ----------------------------------------------------------------------------- text helpers

def has_any(text, words):
    low = (text or "").lower()
    return next((w for w in words if w and w.lower() in low), None)


def shorten(text, limit=180):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def decode_header(value):
    if not value:
        return ""
    parts = []
    for chunk, charset in email.header.decode_header(value):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def html_to_text(markup):
    markup = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def message_text(msg, limit=6000):
    """Plain text of an email (first text/plain part, else text/html converted)."""
    plain, rich = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and rich is None:
            rich = html_to_text(text)
    text = plain if plain and plain.strip() else (rich or "")
    return text[:limit]


def strip_quoted(text):
    """Drop the quoted earlier messages from a reply, keeping only what the sender just wrote."""
    lines = []
    for line in (text or "").splitlines():
        if line.startswith(">"):
            continue
        if re.match(r"^\s*On .{5,120} wrote:\s*$", line) or re.match(r"^\s*-{2,}\s*Original Message", line, re.I) \
                or re.match(r"^\s*From: .+", line) and lines:
            break
        lines.append(line)
    return "\n".join(lines).strip()


DATE_PATTERNS = [
    (r"(?:due(?: date)?|pay by|payment due|by)\s*[:\-]?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", "mdy"),
    (r"(?:due(?: date)?|pay by|payment due|by)\s*[:\-]?\s*([A-Z][a-z]{2,8})\.? (\d{1,2}),? (\d{4})", "Mdy"),
]


def find_due_date(text):
    for pattern, kind in DATE_PATTERNS:
        match = re.search(pattern, text or "", re.I)
        if not match:
            continue
        try:
            if kind == "mdy":
                month, day, year = (int(g) for g in match.groups())
                year += 2000 if year < 100 else 0
                return datetime(year, month, day).strftime("%Y-%m-%d")
            month_name, day, year = match.groups()
            return datetime.strptime("{} {} {}".format(month_name[:3], day, year), "%b %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def find_amount(text):
    match = re.search(r"(?:minimum (?:payment )?due|amount due|total due|balance due)\s*[:\-]?\s*(\$[\d,]+\.\d\d)",
                      text or "", re.I)
    return match.group(1) if match else ""


def display_name(address_header):
    name, addr = email.utils.parseaddr(address_header or "")
    name = decode_header(name).strip('"')
    return name or addr.split("@")[0], addr.lower()


def days_until(iso_date):
    try:
        return (datetime.strptime(iso_date, "%Y-%m-%d").date() - datetime.now().date()).days
    except ValueError:
        return None


# ----------------------------------------------------------------------------- email

def classify_email(msg, me, rules, sent_by_me_later=False, my_name=""):
    """Returns a task dict for an email that needs action, or None. Pure function (easy to test)."""
    subject = decode_header(msg.get("Subject")) or "(no subject)"
    sender_name, sender_addr = display_name(msg.get("From"))
    to_addrs = [a.lower() for _, a in email.utils.getaddresses(msg.get_all("To", []) + msg.get_all("Delivered-To", []))]
    cc_addrs = [a.lower() for _, a in email.utils.getaddresses(msg.get_all("Cc", []))]
    body = strip_quoted(message_text(msg))
    automated = bool(msg.get("List-Unsubscribe") or msg.get("List-Id")
                     or (msg.get("Precedence", "").lower() in ("bulk", "list", "junk")))
    return classify_message(subject, sender_name, sender_addr, to_addrs, cc_addrs, body, me, rules,
                            automated=automated, sent_by_me_later=sent_by_me_later, my_name=my_name)


def classify_message(subject, sender_name, sender_addr, to_addrs, cc_addrs, body, me, rules,
                     automated=False, sent_by_me_later=False, my_name=""):
    """Returns a task dict for a message (email or a Graph mail item) that needs action, or None.

    Provider-neutral: the caller pulls the fields out of whatever it fetched (an email.message.Message
    for IMAP, a JSON message resource for Microsoft Graph) and passes them in here. Pure function
    (easy to test) — this is where "does this need me to do something" is actually decided.
    """
    r = rules["email"]
    sender_addr = (sender_addr or "").lower()
    to_addrs = [a.lower() for a in to_addrs]
    cc_addrs = [a.lower() for a in cc_addrs]
    if sender_addr == me:
        return None
    haystack = "{}\n{}".format(subject, body)
    me_name = me.split("@")[0]

    forced = any(s.lower() in sender_addr for s in r["always_task_from"] if s)
    if not forced:
        if any(s.lower() == sender_addr for s in r["never_task_from"]):
            return None
        if has_any(subject, r["skip_subjects_containing"]):
            return None
        if sent_by_me_later:
            return None

    urgent = has_any(haystack, r["urgent_words"])

    # 1) Alerts, bills, estimates, recalls, doc comments.
    for rule in r["alert_rules"]:
        words = [w.replace("{me}", me_name) for w in rule.get("match", [])]
        if has_any(haystack, words):
            due = find_due_date(body)
            amount = find_amount(body)
            priority = rule.get("priority", "normal")
            left = days_until(due) if due else None
            if urgent or (left is not None and left <= 3):
                priority = "high"
            title = rule.get("title", "{subject}").format(subject=shorten(subject, 70), sender=sender_name)
            if amount and "Pay" in title:
                title = "{} ({} due)".format(title, amount)
            return {"title": title, "category": rule.get("category", "Work"), "priority": priority, "due": due,
                    "note": shorten(body, 200) or subject}

    # 2) Bulk / automated mail that didn't match an alert rule isn't a task.
    automated = bool(automated
                     or re.search(r"(no-?reply|donotreply|do-not-reply|mailer-daemon|notifications?@|alerts?@)",
                                  sender_addr)
                     or has_any(sender_addr, r["skip_senders_containing"]))
    if automated and not forced:
        return None

    # 3) A person asking this person for something.
    direct = me in to_addrs or not (to_addrs or cc_addrs)
    asked = "?" in body[:1500] or has_any(body[:1500], r["request_phrases"]) or has_any(subject, r["request_phrases"])
    names = [n for n in (my_name.strip(), me_name.split(".")[0]) if len(n) >= 2]
    named = any(re.search(r"\b{}\b".format(re.escape(n)), body[:600], re.I) for n in names)
    if forced or (asked and (direct or named)):
        priority = "high" if urgent else "normal"
        due = find_due_date(body)
        if due and (days_until(due) or 99) <= 3:
            priority = "high"
        first = sender_name.split()[0] if sender_name else sender_addr
        return {"title": "Reply to {}: {}".format(first, shorten(re.sub(r"^(re|fw|fwd):\s*", "", subject, flags=re.I), 70)),
                "category": "Work", "priority": priority, "due": due, "note": shorten(body, 220)}
    return None


class EmailChecker:
    def __init__(self, config, store, rules, log=print):
        self.config, self.store, self.rules, self.log = config, store, rules, log
        self.me = (config.get("email_address") or "").strip().lower()
        self.host = (config.get("imap_host") or "").strip()
        self.gmail = self.host.endswith("gmail.com") or self.host.endswith("googlemail.com")

    def connect(self):
        password = get_secret("imap_password")
        if not (self.me and self.host and password):
            raise CheckError("Email isn't set up: add your address, server and app password in Settings.")
        try:
            imap = imaplib.IMAP4_SSL(self.host, int(self.config.get("imap_port") or 993),
                                     ssl_context=ssl.create_default_context(), timeout=30)
        except (OSError, imaplib.IMAP4.error) as error:
            raise CheckError("Couldn't reach the mail server {} ({}). Check the server name and your "
                             "internet connection.".format(self.host, error))
        try:
            imap.login(self.me, password)
        except imaplib.IMAP4.error:
            hint = (" For Gmail, turn on 2-Step Verification and create an app password at "
                    "myaccount.google.com/apppasswords." if self.gmail else
                    " Most providers need an app password here, not your normal password.")
            raise CheckError("The mail server didn't accept the email address and password." + hint)
        return imap

    def check(self):
        imap = self.connect()
        added, closed = 0, 0
        try:
            folder = self.config.get("imap_folder") or "INBOX"
            status, _ = imap.select('"{}"'.format(folder), readonly=True)
            if status != "OK":
                raise CheckError("Couldn't open the mail folder “{}”.".format(folder))
            validity = imap.response("UIDVALIDITY")[1][0]
            state = self.store.get_meta("email_state") or {}
            last_uid = int(state.get("last_uid", 0)) if state.get("validity") == (validity or b"").decode() else 0

            if last_uid:
                criteria = ["UID", "{}:*".format(last_uid + 1)]
            else:
                since = (datetime.now() - timedelta(days=int(self.config.get("first_check_days") or 14)))
                criteria = ["SINCE", since.strftime("%d-%b-%Y")]
            if self.gmail:
                criteria = ["X-GM-RAW", '"category:primary OR category:updates OR is:important"'] + criteria
            typ, data = imap.uid("SEARCH", None, *criteria)
            uids = [int(u) for u in (data[0] or b"").split() if int(u) > last_uid]
            self.log("Email: {} new message{} to look at".format(len(uids), "" if len(uids) == 1 else "s"))

            for uid in uids[-300:]:
                try:
                    task = self._check_one(imap, uid)
                except Exception as error:  # one odd message mustn't stop the rest
                    self.log("Email: skipped message {} ({})".format(uid, error))
                    continue
                if task and not self.store.exists(task["id"]):
                    self.store.add(task)
                    added += 1
            if uids:
                self.store.set_meta("email_state", {"validity": (validity or b"").decode(), "last_uid": max(uids)})
            elif not state:
                self.store.set_meta("email_state", {"validity": (validity or b"").decode(), "last_uid": 0})
            if self.gmail:
                closed = self._close_replied(imap)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return added, closed

    def _check_one(self, imap, uid):
        items = "(RFC822.SIZE BODY.PEEK[HEADER]{})".format(" X-GM-THRID" if self.gmail else "")
        typ, data = imap.uid("FETCH", str(uid), items)
        header_bytes, info = b"", b""
        for part in data:
            if isinstance(part, tuple):
                info, header_bytes = part[0], part[1]
        size = int(re.search(rb"RFC822\.SIZE (\d+)", info).group(1)) if re.search(rb"RFC822\.SIZE (\d+)", info) else 0
        thrid = re.search(rb"X-GM-THRID (\d+)", info)
        thread_id = thrid.group(1).decode() if thrid else ""
        msg = email.message_from_bytes(header_bytes)
        if size and size < 1_500_000:
            typ, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in data if isinstance(p, tuple)), None)
            if raw:
                msg = email.message_from_bytes(raw)
        found = classify_email(msg, self.me, self.rules, my_name=self.config.get("your_name", ""))
        if not found:
            return None
        # Only for would-be tasks (it's a slower lookup): skip threads you've already answered.
        if thread_id and self._replied_since(imap, thread_id, msg):
            if not any(s.lower() in display_name(msg.get("From"))[1] for s in self.rules["email"]["always_task_from"] if s):
                return None
        sender_name, sender_addr = display_name(msg.get("From"))
        when = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        key = thread_id or (msg.get("Message-ID") or str(uid)).strip("<>")
        task = dict(found)
        task.update({
            "id": "e-{}".format(key), "source": "email", "sender": sender_name,
            "url": self.web_link(thread_id, msg), "source_time": when.isoformat() if when else "",
            "extra": {"thread_id": thread_id, "message_id": msg.get("Message-ID", ""), "from": sender_addr},
        })
        return task

    def web_link(self, thread_id, msg):
        if self.gmail and thread_id:
            return "https://mail.google.com/mail/?authuser={}#all/thread-f:{}".format(
                urllib.parse.quote(self.me, safe="@"), thread_id)
        if "office365" in self.host or "outlook" in self.host:
            return "https://outlook.office.com/mail/"
        return ""

    def _replied_since(self, imap, thread_id, msg):
        """Gmail only: did this person send a message in the thread after this one?"""
        try:
            imap.select('"[Gmail]/Sent Mail"', readonly=True)
            typ, data = imap.uid("SEARCH", None, "X-GM-THRID", thread_id)
            uids = (data[0] or b"").split()
            if not uids:
                return False
            typ, data = imap.uid("FETCH", uids[-1], "(INTERNALDATE)")
            sent_at = imaplib.Internaldate2tuple(data[0]) if data and data[0] else None
            received = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
            if not (sent_at and received):
                return False
            return time.mktime(sent_at) > received.timestamp()
        except Exception:
            return False
        finally:
            imap.select('"{}"'.format(self.config.get("imap_folder") or "INBOX"), readonly=True)

    def _close_replied(self, imap):
        """Mark email tasks done when you've replied in the thread since the task was made."""
        closed = 0
        for task in self.store.open_tasks("email"):
            thread_id = task["extra"].get("thread_id")
            if not thread_id:
                continue
            try:
                imap.select('"[Gmail]/Sent Mail"', readonly=True)
                typ, data = imap.uid("SEARCH", None, "X-GM-THRID", thread_id)
                uids = (data[0] or b"").split()
                if not uids:
                    continue
                typ, data = imap.uid("FETCH", uids[-1], "(INTERNALDATE)")
                sent = imaplib.Internaldate2tuple(data[0])
                if sent and time.mktime(sent) > _ts(task.get("source_time") or task["created"]):
                    when = datetime.fromtimestamp(time.mktime(sent), timezone.utc).replace(microsecond=0).isoformat()
                    self.store.set_done(task["id"], True, by="you replied to the email", when=when)
                    closed += 1
            except Exception:
                continue
        imap.select('"{}"'.format(self.config.get("imap_folder") or "INBOX"), readonly=True)
        return closed


# ----------------------------------------------------------------------------- slack

class SlackAPI:
    BASE = "https://slack.com/api/"

    def __init__(self, token):
        self.token = token

    def call(self, method, **params):
        data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
        request = urllib.request.Request(self.BASE + method, data=data, headers={
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/x-www-form-urlencoded"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 2:
                    time.sleep(int(error.headers.get("Retry-After", "5")))
                    continue
                raise CheckError("Slack returned an error ({}).".format(error.code))
            except (urllib.error.URLError, OSError) as error:
                raise CheckError("Couldn't reach Slack ({}).".format(getattr(error, "reason", error)))
            if result.get("ok"):
                return result
            code = result.get("error", "unknown_error")
            if code == "ratelimited" and attempt < 2:
                time.sleep(5)
                continue
            if code in ("invalid_auth", "not_authed", "token_revoked", "account_inactive"):
                raise CheckError("Slack didn't accept the token. Paste a fresh user token (xoxp-…) in Settings.")
            if code == "missing_scope":
                raise CheckError("The Slack token is missing a permission ({}). Add the scopes listed in "
                                 "Settings and reinstall the Slack app.".format(result.get("needed", "")))
            if code == "not_allowed_token_type":
                raise CheckError("That's a bot token. The Task Board needs a user token that starts with xoxp-.")
            raise CheckError("Slack said: {}".format(code))
        raise CheckError("Slack is rate-limiting requests. The next check will try again.")


def clean_slack_text(text, names=None):
    names = names or {}
    text = re.sub(r"<@([A-Z0-9]+)(?:\|([^>]+))?>", lambda m: "@" + (m.group(2) or names.get(m.group(1), "someone")), text or "")
    text = re.sub(r"<#[A-Z0-9]+\|([^>]*)>", r"#\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    return html.unescape(text)


def _is_request_text(text, r):
    words = re.sub(r"@\S+", "", text).strip().lower()
    words = re.sub(r"[!.\s]+$", "", words)
    if not words or words in r["skip_if_only"] or (len(words) < 25 and has_any(words, ["thank", "thx", "ty!"])):
        return False
    return "?" in text or bool(has_any(text, r["request_phrases"]))


def _text_marks_done(text, r):
    """True when a reply reports the work as finished (not just 'Can do!')."""
    low = re.sub(r"<@[^>]+>|@\S+", "", text or "").strip().lower()
    stripped = re.sub(r"[!.\s]+$", "", low)
    if not stripped:
        return False
    if stripped in [a.rstrip("!") for a in r["ack_only_phrases"]]:
        return False
    if any(stripped.startswith(a.rstrip("!")) for a in r["ack_only_phrases"]) and len(stripped) < 16:
        return False
    return bool(has_any(low, r["done_phrases"]))


def is_slack_request(text, rules):
    return _is_request_text(text, rules["slack"])


def reply_marks_done(text, rules):
    return _text_marks_done(text, rules["slack"])


def is_teams_request(text, rules):
    return _is_request_text(text, rules["teams"])


def teams_reply_marks_done(text, rules):
    return _text_marks_done(text, rules["teams"])


def slack_title(text, sender, label="Slack"):
    body = re.sub(r"@\S+", "", text).strip(" ,:-")
    first = re.split(r"(?<=[.?!])\s|\n", body)[0].strip()
    first = re.sub(r"^(hey|hi|hello|yo|morning|good morning)[, !]*", "", first, flags=re.I).strip()
    first = first[:1].upper() + first[1:] if first else "Reply"
    return shorten("{} ({})".format(first, sender.split()[0] if sender else label), 110)


def clean_teams_html(markup):
    """Teams message bodies are HTML with <at id=...>Name</at> mention tags."""
    markup = re.sub(r"(?is)<at[^>]*>(.*?)</at>", lambda m: "@" + m.group(1), markup or "")
    return html_to_text(markup).strip()


class SlackChecker:
    def __init__(self, config, store, rules, log=print):
        self.config, self.store, self.rules, self.log = config, store, rules, log
        token = get_secret("slack_token")
        if not token:
            raise CheckError("Slack isn't set up: paste your Slack user token in Settings.")
        self.api = SlackAPI(token)
        self.names = {}

    def check(self):
        me = self.store.get_meta("slack_me")
        auth = self.api.call("auth.test")
        if not me or me.get("user_id") != auth["user_id"]:
            me = {"user_id": auth["user_id"], "team_url": auth.get("url", "")}
            self.store.set_meta("slack_me", me)
        uid = me["user_id"]

        last = self.store.get_meta("slack_last_ts")
        if last:
            after_day = datetime.fromtimestamp(float(last) - 86400).strftime("%Y-%m-%d")
        else:
            after_day = (datetime.now() - timedelta(days=int(self.config.get("first_check_days") or 14))).strftime("%Y-%m-%d")
        matches = self._search("<@{}> after:{}".format(uid, after_day))
        newest = float(last or 0)
        added = closed = 0
        for m in matches:
            ts = float(m["ts"])
            newest = max(newest, ts)
            if last and ts <= float(last):
                continue
            if m.get("user") == uid or m.get("username") == uid:
                continue
            channel = m.get("channel", {}) or {}
            task_id = "s-{}-{}".format(channel.get("id"), m["ts"])
            if self.store.exists(task_id):
                continue
            text = clean_slack_text(m.get("text", ""))
            if not is_slack_request(text, self.rules):
                continue
            thread_ts = self._thread_ts(m)
            done = self._find_done(uid, channel.get("id"), m["ts"], thread_ts)
            if done:
                continue  # already handled before we ever saw it
            sender = m.get("username") or self._name(m.get("user"))
            urgent = has_any(text, self.rules["slack"]["urgent_words"])
            age_hours = (time.time() - ts) / 3600
            self.store.add({
                "id": task_id, "source": "slack", "title": slack_title(text, sender),
                "note": shorten(text, 240), "sender": "{} · #{}".format(sender, channel.get("name", "dm")),
                "category": "Slack", "priority": "high" if (urgent or age_hours < 12) else "normal",
                "url": m.get("permalink", ""), "source_time": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                "extra": {"channel": channel.get("id"), "ts": m["ts"], "thread_ts": thread_ts},
            })
            added += 1

        # Re-check tasks that are still open: close the ones you've since reported as done.
        for task in self.store.open_tasks("slack"):
            ex = task["extra"]
            if not ex.get("channel"):
                continue
            done = self._find_done(uid, ex["channel"], ex["ts"], ex.get("thread_ts"))
            if done:
                self.store.set_done(task["id"], True, by='you said "{}"'.format(shorten(done["text"], 80)),
                                    when=datetime.fromtimestamp(float(done["ts"]), timezone.utc).isoformat())
                closed += 1
        if newest:
            self.store.set_meta("slack_last_ts", str(newest))
        self.log("Slack: {} new, {} closed".format(added, closed))
        return added, closed

    def _search(self, query):
        results, page = [], 1
        while page <= 5:
            data = self.api.call("search.messages", query=query, sort="timestamp", sort_dir="desc",
                                 count=100, page=page)
            block = data.get("messages", {})
            results.extend(block.get("matches", []))
            if page >= (block.get("paging", {}) or {}).get("pages", 1):
                break
            page += 1
        return results

    @staticmethod
    def _thread_ts(match):
        link = match.get("permalink", "")
        found = re.search(r"thread_ts=([\d.]+)", link)
        return found.group(1) if found else None

    def _find_done(self, uid, channel, ts, thread_ts):
        """Your first message after the request (in its thread, or the same channel) that reports it done."""
        later = []
        try:
            root = thread_ts or ts
            data = self.api.call("conversations.replies", channel=channel, ts=root, limit=200)
            later += [m for m in data.get("messages", []) if m.get("user") == uid and float(m["ts"]) > float(ts)]
        except CheckError:
            pass
        try:
            data = self.api.call("conversations.history", channel=channel, oldest=ts, limit=100)
            later += [m for m in data.get("messages", []) if m.get("user") == uid and float(m["ts"]) > float(ts)]
        except CheckError:
            pass
        for m in sorted(later, key=lambda m: float(m["ts"])):
            if reply_marks_done(m.get("text", ""), self.rules):
                return m
        return None

    def _name(self, user_id):
        if not user_id:
            return "Someone"
        if user_id not in self.names:
            try:
                info = self.api.call("users.info", user=user_id)["user"]
                profile = info.get("profile", {})
                self.names[user_id] = profile.get("display_name") or profile.get("real_name") or info.get("name", user_id)
            except CheckError:
                self.names[user_id] = "Someone"
        return self.names[user_id]


# ----------------------------------------------------------------------------- microsoft (outlook + teams)

# A public, multi-tenant Azure AD app id that Microsoft ships for first-party command-line tools and
# allows the device-code sign-in flow with no client secret. Used so nobody has to register their own
# Azure app just to try this. If your organization blocks it (some IT admins restrict which apps can
# sign in), enter your own app's client id in Settings instead — same sign-in flow either way.
DEFAULT_MS_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
MS_LOGIN_BASE = "https://login.microsoftonline.com"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# Requested together so one sign-in covers both Outlook and Teams; offline_access gets us a refresh
# token so the background checker doesn't need you to sign in again every hour.
MS_SCOPES = "offline_access User.Read Mail.Read Chat.Read ChannelMessage.Read.All"


class GraphAuth:
    """Signs in to a Microsoft account with the device-code flow (no browser redirect, no client
    secret) and keeps a refresh token in the OS keychain so future checks don't need a fresh sign-in.
    """

    def __init__(self, client_id=None, tenant=None):
        self.client_id = (client_id or "").strip() or DEFAULT_MS_CLIENT_ID
        self.tenant = (tenant or "").strip() or "common"

    def _post(self, path, data):
        url = "{}/{}/{}".format(MS_LOGIN_BASE, self.tenant, path)
        body = urllib.parse.urlencode(data).encode()
        request = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except Exception:
                payload = {}
            return {"error": payload.get("error", "http_{}".format(error.code)),
                    "error_description": payload.get("error_description", str(error))}
        except (urllib.error.URLError, OSError) as error:
            raise CheckError("Couldn't reach Microsoft ({}).".format(getattr(error, "reason", error)))

    def start_device_code(self):
        """Returns {verification_uri, user_code, device_code, interval, expires_in, message}."""
        result = self._post("oauth2/v2.0/devicecode", {"client_id": self.client_id, "scope": MS_SCOPES})
        if "error" in result:
            raise CheckError("Microsoft sign-in couldn't start: {}".format(
                result.get("error_description", result["error"])))
        return result

    def poll_device_code(self, device_code, interval=5, expires_in=900):
        """Blocks, polling Microsoft, until the person finishes signing in (or it expires/fails).
        Returns the token response and also saves the refresh token. Call this on a worker thread.
        """
        deadline = time.time() + int(expires_in)
        interval = max(2, int(interval))
        while time.time() < deadline:
            time.sleep(interval)
            result = self._post("oauth2/v2.0/token", {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": self.client_id, "device_code": device_code})
            error = result.get("error")
            if not error:
                self._save(result)
                return result
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "authorization_declined":
                raise CheckError("The Microsoft sign-in was declined.")
            if error == "expired_token":
                raise CheckError("That sign-in code expired. Click Connect Microsoft account to get a new one.")
            raise CheckError("Microsoft sign-in failed: {}".format(result.get("error_description", error)))
        raise CheckError("That sign-in code expired. Click Connect Microsoft account to get a new one.")

    def _save(self, tokens):
        set_secret("ms_refresh_token", tokens.get("refresh_token", ""))

    def is_connected(self):
        return bool(get_secret("ms_refresh_token"))

    def disconnect(self):
        set_secret("ms_refresh_token", "")

    def access_token(self):
        """Exchanges the saved refresh token for a fresh access token. One call per check is fine;
        access tokens are short-lived and there's no benefit to caching them between checks."""
        refresh_token = get_secret("ms_refresh_token")
        if not refresh_token:
            raise CheckError("Microsoft account isn't connected: click Connect Microsoft account in Settings.")
        result = self._post("oauth2/v2.0/token", {
            "grant_type": "refresh_token", "client_id": self.client_id,
            "refresh_token": refresh_token, "scope": MS_SCOPES})
        if "error" in result:
            if result["error"] in ("invalid_grant", "interaction_required"):
                self.disconnect()
                raise CheckError("Your Microsoft sign-in expired. Click Connect Microsoft account in "
                                 "Settings to sign in again.")
            raise CheckError("Microsoft didn't accept the saved sign-in: {}".format(
                result.get("error_description", result["error"])))
        if result.get("refresh_token"):
            self._save(result)
        return result["access_token"]


class GraphAPI:
    """Thin GET/POST wrapper around Microsoft Graph, used by both Outlook and Teams checking."""

    def __init__(self, auth):
        self.auth = auth

    def _request(self, method, url, body=None):
        if not url.startswith("http"):
            url = GRAPH_BASE + url
        token = self.auth.access_token()
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
                message = payload.get("error", {}).get("message", str(error))
                code = payload.get("error", {}).get("code", "")
            except Exception:
                message, code = str(error), ""
            if error.code == 401:
                raise CheckError("Microsoft Graph rejected the request. Try Connect Microsoft account again.")
            if error.code == 403:
                raise CheckError("Microsoft Graph said this account doesn't have permission ({}). Your "
                                 "organization may need an admin to approve Auto AE, or you can enter "
                                 "your own app's client id in Settings.".format(code or message))
            raise CheckError("Microsoft Graph returned an error ({}).".format(code or message))
        except (urllib.error.URLError, OSError) as error:
            raise CheckError("Couldn't reach Microsoft Graph ({}).".format(getattr(error, "reason", error)))

    def get(self, url):
        return self._request("GET", url)

    def post(self, url, body):
        return self._request("POST", url, body)

    def get_pages(self, url, limit=300):
        """Follows @odata.nextLink, returns all "value" items up to limit."""
        items = []
        while url and len(items) < limit:
            page = self.get(url)
            items.extend(page.get("value", []))
            url = page.get("@odata.nextLink")
        return items[:limit]


class OutlookChecker:
    """Checks Outlook/Microsoft 365 mail over Microsoft Graph (not IMAP - Microsoft has been
    turning off basic-auth IMAP access, so this signs in the same way as Teams below)."""

    def __init__(self, config, store, rules, log=print):
        self.config, self.store, self.rules, self.log = config, store, rules, log
        self.auth = GraphAuth(config.get("ms_client_id"), config.get("ms_tenant"))
        self.api = GraphAPI(self.auth)
        self.me = (config.get("ms_account") or "").strip().lower()

    def check(self):
        if not self.auth.is_connected():
            raise CheckError("Outlook isn't connected: click Connect Microsoft account in Settings.")
        me_info = self.api.get("/me?$select=mail,userPrincipalName")
        self.me = (me_info.get("mail") or me_info.get("userPrincipalName") or self.me or "").strip().lower()
        if self.me and self.me != (self.config.get("ms_account") or "").strip().lower():
            self.config["ms_account"] = self.me
            save_config(self.config)

        state = self.store.get_meta("outlook_state") or {}
        last_time = state.get("last_time")
        if last_time:
            since = last_time
        else:
            since = (datetime.now(timezone.utc) - timedelta(
                days=int(self.config.get("first_check_days") or 14))).strftime("%Y-%m-%dT%H:%M:%SZ")
        fields = "id,subject,from,toRecipients,ccRecipients,bodyPreview,body,receivedDateTime,webLink," \
                "conversationId,internetMessageId"
        url = ("/me/mailFolders/Inbox/messages?$filter=receivedDateTime ge {}"
              "&$orderby=receivedDateTime asc&$top=50&$select={}").format(since, fields)
        messages = self.api.get_pages(url, limit=300)
        self.log("Outlook: {} new message{} to look at".format(len(messages), "" if len(messages) == 1 else "s"))

        added = closed = 0
        newest = last_time
        for item in messages:
            when = item.get("receivedDateTime")
            if when and (not newest or when > newest):
                newest = when
            try:
                task = self._check_one(item)
            except Exception as error:
                self.log("Outlook: skipped a message ({})".format(error))
                continue
            if task and not self.store.exists(task["id"]):
                self.store.add(task)
                added += 1
        if newest:
            self.store.set_meta("outlook_state", {"last_time": newest})
        closed = self._close_replied()
        return added, closed

    def _check_one(self, item):
        subject = item.get("subject") or "(no subject)"
        sender = (item.get("from") or {}).get("emailAddress") or {}
        sender_name, sender_addr = sender.get("name") or "", (sender.get("address") or "").lower()
        to_addrs = [(r.get("emailAddress") or {}).get("address", "") for r in item.get("toRecipients", [])]
        cc_addrs = [(r.get("emailAddress") or {}).get("address", "") for r in item.get("ccRecipients", [])]
        body_field = item.get("body") or {}
        raw_body = body_field.get("content") or item.get("bodyPreview") or ""
        body = html_to_text(raw_body) if body_field.get("contentType") == "html" else raw_body
        body = strip_quoted(body)
        found = classify_message(subject, sender_name, sender_addr, to_addrs, cc_addrs, body, self.me,
                                 self.rules, my_name=self.config.get("your_name", ""))
        if not found:
            return None
        conversation_id = item.get("conversationId") or ""
        if conversation_id and self._replied_since(conversation_id, item.get("receivedDateTime")):
            return None
        key = conversation_id or item.get("internetMessageId") or item.get("id")
        task = dict(found)
        task.update({
            "id": "o-{}".format(key), "source": "email", "sender": sender_name or sender_addr,
            "url": item.get("webLink", ""), "source_time": item.get("receivedDateTime", ""),
            "extra": {"conversation_id": conversation_id, "message_id": item.get("id", "")},
        })
        return task

    def _replied_since(self, conversation_id, received_iso):
        """Did I send a message in this conversation after it arrived? (Outlook groups every provider's
        threads by conversationId, so this works the same as the Gmail-only check for IMAP mail.)"""
        try:
            url = ("/me/mailFolders/SentItems/messages?$filter=conversationId eq '{}'"
                  "&$orderby=sentDateTime desc&$top=1&$select=sentDateTime").format(conversation_id)
            sent = self.api.get_pages(url, limit=1)
            if not sent:
                return False
            return sent[0].get("sentDateTime", "") > (received_iso or "")
        except CheckError:
            return False

    def _close_replied(self):
        closed = 0
        for task in self.store.open_tasks("email"):
            conversation_id = task["extra"].get("conversation_id")
            if not conversation_id:
                continue
            try:
                if self._replied_since(conversation_id, task.get("source_time") or task["created"]):
                    self.store.set_done(task["id"], True, by="you replied to the email")
                    closed += 1
            except CheckError:
                continue
        return closed


class TeamsChecker:
    """Finds Teams chat/channel messages that @mention you, using the Microsoft Search API's
    chatMessage entity (the same "mentions me" search Teams itself uses), and closes them out once
    you've replied with something that sounds finished.

    Needs the Chat.Read and ChannelMessage.Read.All permissions granted at sign-in; in some
    organizations ChannelMessage.Read.All needs an admin's one-time approval the first time anyone
    signs in with those scopes - if checks fail with a permission error, that's the most likely fix.
    """

    def __init__(self, config, store, rules, log=print):
        self.config, self.store, self.rules, self.log = config, store, rules, log
        self.auth = GraphAuth(config.get("ms_client_id"), config.get("ms_tenant"))
        self.api = GraphAPI(self.auth)

    def check(self):
        if not self.auth.is_connected():
            raise CheckError("Teams isn't connected: click Connect Microsoft account in Settings.")
        me_info = self.api.get("/me?$select=id,mail,userPrincipalName")
        uid = me_info.get("id")

        last = self.store.get_meta("teams_last_time")
        hits = self._search_mentions()
        added = closed = 0
        newest = last
        for hit in hits:
            msg = hit.get("resource", {}) or {}
            created = msg.get("createdDateTime", "")
            if created and (not newest or created > newest):
                newest = created
            if last and created and created <= last:
                continue
            frm = (msg.get("from") or {}).get("user") or {}
            if frm.get("id") == uid:
                continue
            message_id = msg.get("id")
            if not message_id or self.store.exists("t-{}".format(message_id)):
                continue
            text = clean_teams_html((msg.get("body") or {}).get("content", ""))
            if not is_teams_request(text, self.rules):
                continue
            location = self._location(msg)
            if self._find_done(uid, location, created):
                continue
            sender = frm.get("displayName") or "Someone"
            urgent = has_any(text, self.rules["teams"]["urgent_words"])
            age_hours = self._hours_since(created)
            self.store.add({
                "id": "t-{}".format(message_id), "source": "teams",
                "title": slack_title(text, sender, label="Teams"),
                "note": shorten(text, 240), "sender": "{} · {}".format(sender, location.get("label", "Teams")),
                "category": "Teams", "priority": "high" if (urgent or age_hours < 12) else "normal",
                "url": msg.get("webUrl", ""), "source_time": created,
                "extra": {"chat_id": location.get("chat_id", ""), "team_id": location.get("team_id", ""),
                         "channel_id": location.get("channel_id", ""), "message_id": message_id},
            })
            added += 1

        for task in self.store.open_tasks("teams"):
            ex = task["extra"]
            location = {"chat_id": ex.get("chat_id", ""), "team_id": ex.get("team_id", ""),
                       "channel_id": ex.get("channel_id", "")}
            if not (location["chat_id"] or location["channel_id"]):
                continue
            done = self._find_done(uid, location, task.get("source_time") or task["created"])
            if done:
                self.store.set_done(task["id"], True,
                                    by='you said "{}"'.format(shorten(done.get("text", ""), 80)))
                closed += 1
        if newest:
            self.store.set_meta("teams_last_time", newest)
        self.log("Teams: {} new, {} closed".format(added, closed))
        return added, closed

    def _search_mentions(self):
        body = {"requests": [{"entityTypes": ["chatMessage"], "query": {"queryString": "mentionsMe:true"},
                             "from": 0, "size": 25}]}
        result = self.api.post("/search/query", body)
        hits = []
        for request_result in result.get("value", []):
            for container in request_result.get("hitsContainers", []):
                hits.extend(container.get("hits", []))
        return hits

    @staticmethod
    def _location(msg):
        channel_identity = msg.get("channelIdentity")
        if channel_identity:
            return {"team_id": channel_identity.get("teamId", ""), "channel_id": channel_identity.get("channelId", ""),
                    "chat_id": "", "label": "channel"}
        return {"chat_id": msg.get("chatId", ""), "team_id": "", "channel_id": "", "label": "chat"}

    def _thread_url(self, location, message_id):
        if location.get("channel_id"):
            return "/teams/{}/channels/{}/messages/{}/replies".format(
                location["team_id"], location["channel_id"], message_id)
        return "/chats/{}/messages".format(location["chat_id"])

    def _find_done(self, uid, location, since_iso):
        try:
            if location.get("channel_id"):
                # Channel messages are searched again by mentionsMe won't catch a plain reply, so look
                # at your recent messages in the same channel instead.
                url = "/teams/{}/channels/{}/messages/delta".format(location["team_id"], location["channel_id"])
                items = self.api.get_pages(url, limit=100)
            elif location.get("chat_id"):
                url = "/chats/{}/messages?$orderby=createdDateTime desc&$top=50".format(location["chat_id"])
                items = self.api.get_pages(url, limit=50)
            else:
                return None
        except CheckError:
            return None
        for item in sorted(items, key=lambda m: m.get("createdDateTime", "")):
            frm = (item.get("from") or {}).get("user") or {}
            if frm.get("id") != uid:
                continue
            if since_iso and item.get("createdDateTime", "") <= since_iso:
                continue
            text = clean_teams_html((item.get("body") or {}).get("content", ""))
            if teams_reply_marks_done(text, self.rules):
                return {"text": text, "createdDateTime": item.get("createdDateTime")}
        return None

    @staticmethod
    def _hours_since(iso_time):
        try:
            when = datetime.fromisoformat((iso_time or "").replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - when).total_seconds() / 3600
        except ValueError:
            return 999


# ----------------------------------------------------------------------------- run a check

_check_lock = threading.Lock()


def run_check(store=None, log=print, config=None):
    """Check every enabled source once. Returns a summary dict; problems are listed, not raised."""
    if not _check_lock.acquire(blocking=False):
        return {"added": 0, "closed": 0, "errors": ["A check is already running."], "skipped": True}
    try:
        store = store or TaskStore()
        config = config or load_config()
        summary = {"added": 0, "closed": 0, "errors": []}
        try:
            rules = load_rules()
        except RulesError as error:
            summary["errors"].append(str(error))
            return summary
        sources = []
        if config.get("email_enabled"):
            sources.append(("Email", EmailChecker))
        if config.get("slack_enabled"):
            sources.append(("Slack", SlackChecker))
        if config.get("outlook_enabled"):
            sources.append(("Outlook", OutlookChecker))
        if config.get("teams_enabled"):
            sources.append(("Teams", TeamsChecker))
        if not sources:
            summary["errors"].append("Nothing to check yet. Turn on a source in Settings.")
        for label, checker in sources:
            try:
                added, closed = checker(config, store, rules, log).check()
                summary["added"] += added
                summary["closed"] += closed
            except CheckError as error:
                summary["errors"].append("{}: {}".format(label, error))
            except Exception as error:
                summary["errors"].append("{}: unexpected problem ({}: {})".format(label, type(error).__name__, error))
        store.set_meta("last_check", {"at": now_iso(), "errors": summary["errors"],
                                      "added": summary["added"], "closed": summary["closed"]})
        return summary
    finally:
        _check_lock.release()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Task Board: email + Slack to-do list")
    parser.add_argument("--check", action="store_true", help="check email and Slack once")
    parser.add_argument("--list", action="store_true", help="print open tasks")
    args = parser.parse_args()
    if args.check:
        result = run_check()
        print("Added {added}, closed {closed}".format(**result))
        for problem in result["errors"]:
            print("!", problem)
        sys.exit(1 if result["errors"] else 0)
    if args.list or not args.check:
        for t in TaskStore().open_tasks():
            print("[{:6}] {}{}  ({})".format(t["priority"], t["title"], "  due " + t["due"] if t["due"] else "",
                                             t["sender"] or t["source"]))
