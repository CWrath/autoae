"""Auto AE for macOS: first-launch setup, updates, and start-up.

Contents/MacOS/AutoAE runs this with the Mac's own Python 3. It:

1. Copies the app out of the .app bundle into ~/Library/Application Support/AUTO_AE/app.
   The tools save their settings next to their code, and macOS can run a downloaded app
   from a read-only location. Files the app creates itself are never touched, and folder
   templates are only added when missing, so updating keeps your settings.
2. Creates a Python environment in .../AUTO_AE/venv and installs requirements-macos.txt
   into it, with a progress window. This happens on first launch and again only when that
   list or the Python version changes.
3. Starts main.py with that environment. If the app fails to start, a dialog says why
   instead of the app silently disappearing. Output goes to .../AUTO_AE/autoae.log.

Standard library only, because it runs before anything is installed.
"""

import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

SETUP_VERSION = 1  # bump to force every Mac to rebuild its environment

RESOURCES = Path(__file__).resolve().parent
BUNDLED_APP = RESOURCES / "app"
MANIFEST = RESOURCES / "app-manifest.json"
REQUIREMENTS = RESOURCES / "requirements-macos.txt"

SUPPORT = Path(os.environ.get("AUTOAE_SUPPORT_DIR") or Path.home() / "Library" / "Application Support" / "AUTO_AE")
RUN_DIR = SUPPORT / "app"
INSTALLED_MANIFEST = RUN_DIR / ".bundle-manifest.json"
VENV = SUPPORT / "venv"
VENV_PYTHON = VENV / "bin" / "python3"
SETUP_STAMP = VENV / "autoae-setup.json"
SETUP_LOG = SUPPORT / "setup.log"
APP_LOG = SUPPORT / "autoae.log"
PYTHON_CHOICE = SUPPORT / "python-path.txt"
RELEASE_UPDATE = SUPPORT / "release-update"

IS_MAC = sys.platform == "darwin"
OPTIONAL_MARKER = "# --- optional ---"


# ---------------------------------------------------------------------------- helpers
def active_app_resources():
    """Prefer the verified updater overlay to the resources in the app bundle."""
    manifest = RELEASE_UPDATE / "app-manifest.json"
    app = RELEASE_UPDATE / "app"
    requirements = RELEASE_UPDATE / "requirements-macos.txt"
    if manifest.is_file() and app.is_dir() and requirements.is_file():
        return app, manifest, requirements
    return BUNDLED_APP, MANIFEST, REQUIREMENTS


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, data):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def show_dialog(title, message, log_path=None):
    """A native alert that works even if Tk is what failed. Returns quietly if it can't be shown."""
    if not IS_MAC or not shutil.which("osascript"):
        print("{}: {}".format(title, message), file=sys.stderr)
        return

    def quoted(text):
        return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    buttons = '{"Show Log", "OK"}' if log_path else '{"OK"}'
    script = "button returned of (display dialog {} with title {} buttons {} default button \"OK\" with icon stop)".format(
        quoted(message), quoted(title), buttons)
    try:
        choice = subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()
        if choice == "Show Log" and log_path:
            subprocess.run(["open", "-e", str(log_path)])
    except OSError:
        pass


# ---------------------------------------------------------------------------- 1. app files
def sync_app():
    """Copy the bundled app into RUN_DIR if this build isn't there yet. Returns True if files changed."""
    app_source, manifest_source, _ = active_app_resources()
    manifest = read_json(manifest_source, None)
    if manifest is None:
        raise RuntimeError("The app bundle is incomplete ({} is missing). Download Auto AE again.".format(
            manifest_source.name))
    installed = read_json(INSTALLED_MANIFEST, {"build": None, "files": {}, "seed": []})
    if installed.get("build") == manifest["build"] and (RUN_DIR / "main.py").is_file():
        return False

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    seeds = set(manifest.get("seed", []))
    for rel, digest in manifest["files"].items():
        target = RUN_DIR / rel
        if rel in seeds and target.exists():
            continue  # a starter file the user may have edited (folder templates)
        if target.is_file() and sha256(target) == digest:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(app_source / rel, target)
        if manifest.get("executable") and rel in manifest["executable"]:
            target.chmod(0o755)

    # Remove code that an older build installed and this one no longer has. Anything the
    # app wrote itself was never in a manifest, so it stays.
    old_seeds = set(installed.get("seed", []))
    for rel in installed.get("files", {}):
        if rel not in manifest["files"] and rel not in old_seeds:
            try:
                (RUN_DIR / rel).unlink()
            except OSError:
                pass

    write_json(INSTALLED_MANIFEST, manifest)
    return True


# ---------------------------------------------------------------------------- 2. packages
def package_name(requirement):
    """"pandas>=2.0,<4" -> "pandas"."""
    name = requirement.split(";")[0].strip()
    for separator in "<>=!~ [(":
        name = name.split(separator)[0]
    return name


def read_requirements():
    """(required, optional) requirement strings from requirements-macos.txt."""
    text = active_app_resources()[2].read_text(encoding="utf-8")
    required_text, _, optional_text = text.partition(OPTIONAL_MARKER)

    def lines(block):
        return [line.split("#", 1)[0].strip() for line in block.splitlines() if line.split("#", 1)[0].strip()]
    return lines(required_text), lines(optional_text)


def wanted_stamp():
    return {
        "setup_version": SETUP_VERSION,
        "python": sys.executable,
        "python_version": "{}.{}".format(*sys.version_info[:2]),
        "requirements": hashlib.sha256(active_app_resources()[2].read_bytes()).hexdigest(),
    }


def environment_ready():
    stamp = read_json(SETUP_STAMP, {})
    wanted = wanted_stamp()
    if any(stamp.get(key) != value for key, value in wanted.items()):
        return False
    return VENV_PYTHON.exists() and os.access(str(VENV_PYTHON), os.X_OK)


class Setup:
    """Builds the environment on a worker thread and reports progress through a queue."""

    def __init__(self):
        self.events = queue.Queue()
        self.process = None
        self.cancelled = False
        self.optional_failed = []

    # worker thread ---------------------------------------------------------
    def run(self):
        try:
            SUPPORT.mkdir(parents=True, exist_ok=True)
            with open(SETUP_LOG, "w", encoding="utf-8") as self.log:
                self.log.write("Auto AE setup {}\nPython {} ({})\n\n".format(
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sys.version.split()[0], sys.executable))
                self.build()
            self.events.put(("done", None))
        except Exception as exc:  # reported in the window
            if self.cancelled:
                return
            try:
                with open(SETUP_LOG, "a", encoding="utf-8") as log:
                    log.write("\n" + traceback.format_exc())
            except OSError:
                pass
            self.events.put(("failed", str(exc)))

    def build(self):
        required, optional = read_requirements()
        self.status("Creating Auto AE's Python environment…", 0.03)
        if SETUP_STAMP.exists():
            SETUP_STAMP.unlink()
        self.call([sys.executable, "-m", "venv", "--clear", str(VENV)], "Couldn't create the Python environment.")

        pip = [str(VENV_PYTHON), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
               "--progress-bar", "off", "--prefer-binary"]
        self.status("Updating pip…", 0.08)
        self.call(pip + ["--upgrade", "pip"], None)  # an old pip still works, so failures are fine

        self.status("Downloading packages…", 0.12)
        self.call(pip + required, "Couldn't install the packages Auto AE needs.", progress=(0.12, 0.85, len(required) + 12))

        for index, requirement in enumerate(optional):
            name = package_name(requirement)
            self.status("Installing {}…".format(name), 0.85 + 0.12 * index / max(1, len(optional)))
            if not self.call(pip + [requirement], None):
                self.optional_failed.append(name)

        stamp = wanted_stamp()
        stamp["optional_failed"] = self.optional_failed
        stamp["finished"] = datetime.now().isoformat(timespec="seconds")
        write_json(SETUP_STAMP, stamp)
        PYTHON_CHOICE.write_text(sys.executable, encoding="utf-8")
        self.status("Starting Auto AE…", 1.0)

    def call(self, command, error, progress=None):
        """Run a command, streaming its output to the log. Raises with `error` on failure unless error is None."""
        self.log.write("$ {}\n".format(" ".join(command)))
        self.log.flush()
        env = dict(os.environ, PYTHONUNBUFFERED="1", PIP_NO_INPUT="1")
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, errors="replace", env=env)
        collected = 0
        last_error = ""
        offline = False
        for line in self.process.stdout:
            self.log.write(line)
            line = line.strip()
            if not line:
                continue
            if line.startswith("ERROR:") and not line.startswith("ERROR: Ignored the following"):
                last_error = line
            if any(sign in line for sign in ("NewConnectionError", "Failed to establish a new connection",
                                             "Temporary failure in name resolution", "nodename nor servname")):
                offline = True
            self.events.put(("detail", line))
            if progress and line.startswith("Collecting "):
                collected += 1
                start, end, expected = progress
                self.status("Downloading {}…".format(package_name(line.split()[1])),
                            start + (end - start) * min(1.0, collected / expected))
            elif progress and line.startswith("Installing collected packages"):
                self.status("Installing packages…", progress[1], busy=True)
        code = self.process.wait()
        self.log.write("[exit {}]\n\n".format(code))
        self.log.flush()
        if self.cancelled:
            raise RuntimeError("Setup was cancelled.")
        if code != 0 and error:
            if offline:
                raise RuntimeError("Auto AE couldn't reach the internet to download its packages.")
            if len(last_error) > 160:
                last_error = last_error[:157] + "…"
            raise RuntimeError(error + ("\n" + last_error if last_error else ""))
        return code == 0

    def status(self, text, fraction, busy=False):
        self.events.put(("status", (text, fraction, busy)))

    def cancel(self):
        self.cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()


def run_setup_window():
    """Show the setup window until the environment is ready. Returns True when it is."""
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk

    setup = Setup()
    result = {"ok": False}
    shown = {"fraction": 0.0}

    root = tk.Tk()
    root.title("Auto AE")
    root.resizable(False, False)
    family = tkfont.nametofont("TkDefaultFont").cget("family")
    title_font = tkfont.Font(root, family=family, size=16, weight="bold")
    small_font = tkfont.Font(root, family=family, size=11)
    icon_path = BUNDLED_APP / "assets" / "autoae.png"
    try:
        root._icon = tk.PhotoImage(file=str(icon_path))
        root.iconphoto(True, root._icon)
        logo = root._icon.subsample(max(1, root._icon.width() // 64))
    except tk.TclError:
        logo = None

    frame = ttk.Frame(root, padding=(24, 22, 24, 18))
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)
    if logo:
        ttk.Label(frame, image=logo).grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 16))
    title = ttk.Label(frame, text="Setting up Auto AE", font=title_font)
    title.grid(row=0, column=1, sticky="w")
    intro = ("Auto AE is installing the Python packages it needs. This only happens the first "
             "time and after updates, and takes a few minutes with an internet connection.")
    message = ttk.Label(frame, wraplength=380, justify="left", text=intro)
    message.grid(row=1, column=1, sticky="w", pady=(4, 14))

    bar = ttk.Progressbar(frame, length=380, maximum=1.0, mode="determinate")
    bar.grid(row=2, column=1, sticky="ew")
    status = ttk.Label(frame, text="Starting…")
    status.grid(row=3, column=1, sticky="w", pady=(6, 0))
    detail = ttk.Label(frame, text="", foreground="gray", font=small_font)
    detail.grid(row=4, column=1, sticky="w")

    buttons = ttk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
    log_button = ttk.Button(buttons, text="Show Log", command=lambda: open_path(SETUP_LOG))
    retry_button = ttk.Button(buttons, text="Try Again")
    quit_button = ttk.Button(buttons, text="Cancel")
    quit_button.pack(side="right")

    def quit_setup():
        setup.cancel()
        root.destroy()

    def start():
        setup.cancelled = False
        setup.optional_failed = []
        title.configure(text="Setting up Auto AE")
        message.configure(text=intro)
        status.configure(text="Starting…")
        detail.configure(text="")
        shown["fraction"] = 0.0
        bar.configure(mode="determinate", value=0)
        retry_button.pack_forget()
        log_button.pack_forget()
        quit_button.configure(text="Cancel")
        threading.Thread(target=setup.run, daemon=True).start()
        root.after(100, poll)

    def poll():
        try:
            while True:
                kind, value = setup.events.get_nowait()
                if kind == "status":
                    text, fraction, busy = value
                    status.configure(text=text)
                    if busy:
                        if str(bar.cget("mode")) != "indeterminate":
                            bar.configure(mode="indeterminate")
                            bar.start(12)
                    else:
                        bar.stop()
                        shown["fraction"] = max(shown["fraction"], fraction)
                        bar.configure(mode="determinate", value=shown["fraction"])
                elif kind == "detail":
                    detail.configure(text=value if len(value) < 70 else value[:67] + "…")
                elif kind == "done":
                    result["ok"] = True
                    if setup.optional_failed:
                        # e.g. no ready-made package for this Python: only the tool that needs it is missing
                        detail.configure(text="Couldn't install {} (see setup.log). The other tools are ready.".format(
                            ", ".join(setup.optional_failed)))
                        root.after(4000, root.destroy)
                    else:
                        root.after(300, root.destroy)
                    return
                elif kind == "failed":
                    bar.stop()
                    bar.configure(mode="determinate", value=0)
                    title.configure(text="Setup couldn't finish")
                    message.configure(text=value)
                    status.configure(text="Try again, or open the log for the details.")
                    detail.configure(text="")
                    quit_button.configure(text="Quit")
                    retry_button.pack(side="right", padx=(0, 8))
                    log_button.pack(side="right", padx=(0, 8))
                    return
        except queue.Empty:
            pass
        root.after(100, poll)

    retry_button.configure(command=start)
    quit_button.configure(command=quit_setup)
    root.protocol("WM_DELETE_WINDOW", quit_setup)
    root.update_idletasks()
    width, height = max(root.winfo_reqwidth(), 480), root.winfo_reqheight()
    root.geometry("+{}+{}".format((root.winfo_screenwidth() - width) // 2, (root.winfo_screenheight() - height) // 3))
    root.lift()
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))
    start()
    root.mainloop()
    return result["ok"]


def open_path(path):
    if IS_MAC:
        subprocess.Popen(["open", "-e", str(path)])
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(path)])


# ---------------------------------------------------------------------------- 3. start the app
def launch():
    """Replace this process with the environment's Python running the app (see run_app)."""
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), "--run"])


def run_app():
    """Runs inside the environment: start main.py, and explain failures instead of vanishing."""
    os.chdir(RUN_DIR)
    if getattr(sys, "frozen", False):
        # sys.path[0] is the frozen runtime's base_library.zip; replacing it breaks stdlib imports
        sys.path.insert(0, str(RUN_DIR))
        sys.argv = [str(RUN_DIR / "main.py")] + [a for a in sys.argv[1:] if a != "--run"]
    else:
        sys.path[0] = str(RUN_DIR)
        sys.argv = [str(RUN_DIR / "main.py")]
    if not sys.stdout or not sys.stdout.isatty():
        if APP_LOG.exists():
            os.replace(APP_LOG, APP_LOG.with_name("autoae.previous.log"))
        log = open(APP_LOG, "w", encoding="utf-8", buffering=1)
        os.dup2(log.fileno(), 1)  # so messages from Tk and other C code land in the log too
        os.dup2(log.fileno(), 2)
        sys.stdout = sys.stderr = log
    import faulthandler
    faulthandler.enable()  # a hard crash still leaves a traceback in the log
    faulthandler.cancel_dump_traceback_later()  # the launcher's start-up hang tracer; start-up succeeded
    print("Auto AE {} · Python {} · {}".format(
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sys.version.split()[0], sys.executable))
    try:
        import runpy
        runpy.run_path(str(RUN_DIR / "main.py"), run_name="__main__")
    except SystemExit:
        raise
    except BaseException as exc:
        traceback.print_exc()
        sys.stdout.flush()
        advice = "The full error is in autoae.log."
        if isinstance(exc, ImportError):
            if getattr(sys, "frozen", False):
                advice = "Download Auto AE again in case its bundled files are damaged. The full error is in autoae.log."
            else:
                try:
                    SETUP_STAMP.unlink()
                except OSError:
                    pass
                advice = "Open Auto AE again to reinstall its packages. The full error is in autoae.log."
        show_dialog("Auto AE couldn't start", "{}: {}\n\n{}".format(type(exc).__name__, exc, advice), APP_LOG)
        sys.exit(1)


def main():
    if "--run" in sys.argv[1:]:
        run_app()
        return
    try:
        sync_app()
        if getattr(sys, "frozen", False):
            run_app()
            return
        if not environment_ready():
            if not run_setup_window():
                return
        launch()
    except Exception as exc:
        traceback.print_exc()
        show_dialog("Auto AE couldn't start", str(exc), SETUP_LOG if SETUP_LOG.exists() else None)
        sys.exit(1)


if __name__ == "__main__":
    main()
