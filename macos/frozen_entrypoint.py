import multiprocessing
import os
import runpy
import sys
import traceback
from datetime import datetime
from pathlib import Path


def record_startup_failure():
    """A failure before bootstrap.py takes over would otherwise just bounce in the Dock."""
    support = Path(os.environ.get("AUTOAE_SUPPORT_DIR") or Path.home() / "Library" / "Application Support" / "AUTO_AE")
    try:
        support.mkdir(parents=True, exist_ok=True)
        with open(support / "launch-error.log", "a", encoding="utf-8") as log:
            log.write("{}\n{}\n".format(datetime.now().isoformat(timespec="seconds"), traceback.format_exc()))
    except OSError:
        pass


def start_hang_tracer():
    """Dump every thread's stack to launch-hang.log if start-up takes over 20 seconds."""
    import faulthandler
    support = Path(os.environ.get("AUTOAE_SUPPORT_DIR") or Path.home() / "Library" / "Application Support" / "AUTO_AE")
    try:
        support.mkdir(parents=True, exist_ok=True)
        handle = open(support / "launch-hang.log", "w", encoding="utf-8")
        handle.write("Started {}\n".format(datetime.now().isoformat(timespec="seconds")))
        handle.flush()
        faulthandler.dump_traceback_later(20, repeat=False, file=handle)
        return handle, faulthandler
    except OSError:
        return None, faulthandler


if __name__ == "__main__":
    multiprocessing.freeze_support()
    tracer_handle, tracer = start_hang_tracer()
    try:
        bundle_resources = Path(getattr(sys, "_MEIPASS")) / "bundle"
        app_bundle = Path(sys.executable).resolve().parent.parent.parent
        os.environ["AUTOAE_APP_BUNDLE"] = str(app_bundle)
        runpy.run_path(str(bundle_resources / "bootstrap.py"), run_name="__main__")
    except SystemExit:
        raise
    except BaseException:
        record_startup_failure()
        raise
