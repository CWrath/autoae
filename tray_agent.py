"""Auto AE's tray agent: keeps Auto Ingest watching and scheduled backups running with no window open.
Started with `main.py --tray` (the app does this when it closes while something is running)."""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import background_services as bs

POLL_SECONDS = 2.0
RETRY_SECONDS = 30
IDLE_EXIT_SECONDS = 20

logger = logging.getLogger("autoae.tray")


def log_file():
    return bs.data_dir() / "tray_agent.log"


def setup_logging():
    handler = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def stamp_of(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


class Backends:
    """Imported after logging is set up, because the tool modules configure logging when imported."""

    def __init__(self):
        from tools import projectbackuptool, watch_folder_sorter
        self.backup = projectbackuptool
        self.sorter = watch_folder_sorter

        class HeadlessIngest(watch_folder_sorter.IngestEngine):
            def __init__(self):
                self._init_engine()

            def log(self, message):
                logger.info(message)

        self.engine = HeadlessIngest()


class Agent:
    def __init__(self, backends=None):
        self.backends = backends or Backends()
        self.stop_event = threading.Event()
        self.icon = None
        self.scheduler = None
        self.observer = None
        self.ingest_stamp = None
        self.ingest_retry = 0.0
        self.ingest_text = "Auto Ingest: off"
        self.backup_stamp = None
        self.backup_retry = 0.0
        self.backup_scheduled = False
        self.backup_state = ""
        self.backup_lock = threading.Lock()
        self.idle_since = None
        self.notified = set()
        import schedule
        self.scheduler = schedule.Scheduler()

    # ---- Auto Ingest
    def sync_ingest(self, want):
        if not want:
            if self.observer is not None or self.ingest_stamp is not None:
                self.stop_ingest()
            self.ingest_text = "Auto Ingest: off"
            return
        sorter = self.backends.sorter
        stamp = stamp_of(sorter.RULES_FILE)
        if stamp == self.ingest_stamp and (self.observer is not None or time.monotonic() < self.ingest_retry):
            return
        self.stop_ingest()
        self.ingest_stamp = stamp
        try:
            rules = sorter.load_rules(sorter.RULES_FILE)
            problems = sorter.validate_rules(rules) if rules else ["No Auto Ingest rules are saved."]
            if not problems:
                self.observer = self.backends.engine.start_observer(rules)
        except (OSError, ValueError, RuntimeError) as error:
            problems = [str(error)]
        if problems:
            self.ingest_retry = time.monotonic() + RETRY_SECONDS
            self.ingest_text = "Auto Ingest: needs attention (see log)"
            logger.error("Auto Ingest can't start: %s", "; ".join(problems))
            self.notify("Auto Ingest can't start", problems[0])
            return
        self.ingest_text = "Auto Ingest: watching {} rule(s)".format(len(rules))
        logger.info("Watching %d rule(s)", len(rules))

    def stop_ingest(self):
        observer, self.observer = self.observer, None
        self.ingest_stamp = None
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)
            logger.info("Stopped watching")

    # ---- Backup
    def load_backup_config(self):
        with open(self.backends.backup.CONFIG_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def sync_backup(self, want):
        if not want:
            if self.backup_scheduled:
                self.scheduler.clear()
                self.backup_scheduled = False
            self.backup_stamp = None
            self.backup_state = ""
            return
        module = self.backends.backup
        stamp = stamp_of(module.CONFIG_FILE)
        if stamp == self.backup_stamp and (self.backup_scheduled or time.monotonic() < self.backup_retry):
            return
        self.backup_stamp = stamp
        self.scheduler.clear()
        self.backup_scheduled = False
        try:
            config = self.load_backup_config()
            problem = module.config_problem(config)
            if not problem:
                module.add_backup_job(self.scheduler, config, self.start_backup_thread)
                self.backup_scheduled = True
        except (OSError, ValueError) as error:
            problem = str(error)
        if problem:
            self.backup_retry = time.monotonic() + RETRY_SECONDS
            self.backup_state = "needs attention (see log)"
            logger.error("Scheduled backup can't start: %s", problem)
            self.notify("Scheduled backup can't start", problem)
        else:
            self.backup_state = ""
            logger.info("Backups scheduled")

    def start_backup_thread(self):
        threading.Thread(target=self.run_backup_job, daemon=True).start()

    def run_backup_job(self):
        if not self.backup_lock.acquire(blocking=False):
            logger.info("A backup is already running; skipped this one")
            return
        module = self.backends.backup
        try:
            self.backup_state = "running..."
            self.refresh_menu()
            config = self.load_backup_config()
            problem = module.config_problem(config)
            if problem:
                failures, detail = ["config"], problem
                logger.error("Backup can't run: %s", problem)
            else:
                logger.info("Backup started")
                failures = module.run_backup(config, logger.info)
                detail = "see the log"
            stamp = datetime.now().strftime("%a %H:%M")
            if failures:
                self.backup_state = "last run {} had errors".format(stamp)
                self.notify("Backup finished with errors", detail)
            else:
                self.backup_state = "last run {} OK".format(stamp)
                logger.info("Backup finished")
        except Exception:
            logger.exception("Backup crashed")
            self.backup_state = "last run failed"
            self.notify("Backup failed", "See the log for details.")
        finally:
            self.backup_lock.release()
            self.refresh_menu()

    @property
    def backup_text(self):
        if not bs.get_services()["backup"]:
            return "Backup: off"
        parts = []
        if self.backup_scheduled:
            next_run = self.scheduler.next_run
            if next_run:
                parts.append("next {:%a %H:%M}".format(next_run))
        if self.backup_state:
            parts.append(self.backup_state)
        return "Backup: " + (", ".join(parts) or "starting...")

    # ---- loop
    def tick(self):
        services = bs.get_services()
        self.sync_ingest(services["auto_ingest"])
        self.sync_backup(services["backup"])
        self.scheduler.run_pending()
        self.refresh_menu()
        if any(services.values()):
            self.idle_since = None
        elif self.idle_since is None:
            self.idle_since = time.monotonic()
        elif time.monotonic() - self.idle_since >= IDLE_EXIT_SECONDS:
            logger.info("Nothing left to run; exiting")
            self.shutdown()

    def worker(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Background check failed")
            self.stop_event.wait(POLL_SECONDS)

    # ---- tray
    def notify(self, title, message):
        key = (title, message)
        if key in self.notified or self.icon is None:
            return
        self.notified.add(key)
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    def refresh_menu(self):
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def shutdown(self):
        self.stop_event.set()
        self.stop_ingest()
        if self.icon is not None:
            self.icon.stop()

    def quit(self):
        bs.set_service("auto_ingest", False)
        bs.set_service("backup", False)
        self.shutdown()

    def open_app(self):
        if bs.is_running(bs.APP_PORT):
            self.notify("Auto AE", "Auto AE is already open.")
        else:
            bs.launch_app()

    def open_log(self):
        path = str(log_file())
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def build_menu(self, pystray):
        def act(function):
            return lambda icon, item: function()

        item = pystray.MenuItem
        return pystray.Menu(
            item("Auto AE background tasks", None, enabled=False),
            item(lambda i: self.ingest_text, None, enabled=False),
            item(lambda i: self.backup_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Run backup now", act(self.start_backup_thread), enabled=lambda i: not self.backup_lock.locked()),
            item("Stop Auto Ingest", act(lambda: bs.set_service("auto_ingest", False)),
                 visible=lambda i: bs.get_services()["auto_ingest"]),
            item("Stop scheduled backups", act(lambda: bs.set_service("backup", False)),
                 visible=lambda i: bs.get_services()["backup"]),
            pystray.Menu.SEPARATOR,
            item("Open Auto AE", act(self.open_app), default=True),
            item("Open log", act(self.open_log)),
            item("Quit", act(self.quit)),
        )

    def run(self, pystray, image_module):
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        try:
            image = image_module.open(root / "assets" / "autoae.png")
        except OSError:
            image = image_module.new("RGB", (64, 64), "#3b82f6")
        self.icon = pystray.Icon("autoae", image, "Auto AE", self.build_menu(pystray))
        threading.Thread(target=self.worker, daemon=True).start()
        self.icon.run()
        self.stop_event.set()
        self.stop_ingest()


def main():
    lock = bs.PortLock(bs.AGENT_PORT)
    if not lock.acquire():
        return 0
    setup_logging()
    try:
        import pystray
        from PIL import Image
    except ImportError as error:
        logger.error("The tray icon needs pystray and Pillow: %s", error)
        return 1
    logger.info("Tray agent started")
    try:
        Agent().run(pystray, Image)
    except Exception:
        logger.exception("Tray agent crashed")
        return 1
    finally:
        lock.release()
    logger.info("Tray agent stopped")
    return 0
