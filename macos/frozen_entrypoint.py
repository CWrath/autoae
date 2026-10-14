import os
import runpy
import sys
from pathlib import Path


bundle_resources = Path(getattr(sys, "_MEIPASS")) / "bundle"
app_bundle = Path(sys.executable).resolve().parent.parent.parent
os.environ["AUTOAE_APP_BUNDLE"] = str(app_bundle)
runpy.run_path(str(bundle_resources / "bootstrap.py"), run_name="__main__")
