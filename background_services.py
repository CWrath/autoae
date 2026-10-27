"""Shared plumbing between Auto AE and its tray agent: which services run in the background,
single-instance locks, and starting the agent. No GUI imports."""
import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

SERVICES = ("auto_ingest", "backup")
AGENT_PORT = 53683
APP_PORT = 53684
ROOT = Path(__file__).resolve().parent


def data_dir():
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "AUTO_AE"
    else:
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AUTO_AE"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file():
    return data_dir() / "background_services.json"


def get_services():
    try:
        saved = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    return {name: bool(saved.get(name, False)) for name in SERVICES}


def set_service(name, enabled):
    if name not in SERVICES:
        raise ValueError("Unknown service: {}".format(name))
    services = get_services()
    if services[name] == bool(enabled):
        return
    services[name] = bool(enabled)
    settings_file().write_text(json.dumps(services, indent=2), encoding="utf-8")


class PortLock:
    """Holds a localhost port for as long as the process lives, so only one process owns it."""

    def __init__(self, port):
        self.port = port
        self._socket = None

    def acquire(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", self.port))
            probe.listen(1)
        except OSError:
            probe.close()
            return False
        self._socket = probe
        return True

    def release(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None


def is_running(port):
    """True while some process holds the port (a PortLock). Probes by trying to take it."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        return True
    finally:
        probe.close()
    return False


def tray_available():
    return importlib.util.find_spec("pystray") is not None


def _spawn(arguments):
    if getattr(sys, "frozen", False):
        command = [sys.executable] + arguments
    else:
        executable = sys.executable
        if sys.platform == "win32" and executable.lower().endswith("python.exe"):
            windowless = executable[:-len("python.exe")] + "pythonw.exe"
            if os.path.exists(windowless):
                executable = windowless
        command = [executable, str(ROOT / "main.py")] + arguments
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "cwd": str(ROOT)}
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: outlives the app that started it
        options["creationflags"] = 0x00000008 | 0x00000200
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def launch_agent():
    return _spawn(["--tray"])


def launch_app():
    return _spawn([])


def handover(name):
    """Let the tray agent carry on `name` after the app closes. False when that isn't possible."""
    if not tray_available():
        return False
    set_service(name, True)
    if not is_running(AGENT_PORT):
        try:
            launch_agent()
        except OSError:
            set_service(name, False)
            return False
    return True


def agent_has(name):
    return get_services().get(name, False) and is_running(AGENT_PORT)
