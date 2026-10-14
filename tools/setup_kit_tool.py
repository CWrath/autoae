"""Setup Kit: carry your editing setup to any machine.

A kit is a plain folder (on a USB drive, Dropbox, a NAS...) or a .zip of one. "Capture" copies
shortcuts, workspaces, presets and preferences from this machine into the kit; "Deploy" puts
them into the right places on the machine you're sitting at, installs After Effects plug-ins and
extensions, and runs any installers you've added. Existing files are backed up before anything
is overwritten.

Kit layout (you can add files by hand too):

    kit.json                         when/where it was captured, which shortcut set is active
    after_effects/shortcuts/         aeks/*.txt
    after_effects/workspaces/        ModifiedWorkspaces/, OriginalUserWorkspaces/
    after_effects/scripts/           Scripts/ (ScriptUI Panels, Startup...)
    after_effects/presets/           User Presets/ (.ffx)
    after_effects/preferences/       Adobe After Effects {VERSION} Prefs*.txt
    after_effects/plugins/windows/   .aex files or plug-in folders  -> MediaCore
    after_effects/plugins/mac/       .plugin bundles or folders     -> MediaCore
    after_effects/extensions/        CEP extension folders or .zxp files
    premiere_pro/...                 shortcuts (.kys), workspaces, effect presets, preferences
    media_encoder/presets/           .epr export presets
    avid/users/, avid/site_settings/
    installers/windows/, installers/mac/   run in name order (01_..., 02_...)
"""
import filecmp
import getpass
import json
import os
import platform
import plistlib
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox

import customtkinter as ctk

import error_help
import theme
from base_tool import BaseTool
from theme import C

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(APP_DIR, "setup_kit_settings.json")
MANIFEST = "kit.json"
VERSION_TOKEN = "{VERSION}"
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}
AVID_NOT_USERS = {"Avid FatalErrorReports", "Reports", "Settings"}


def version_key(version):
    return tuple(int(part) if part.isdigit() else 0 for part in str(version).split("."))


# --------------------------------------------------------------------------- where things live
def _documents_folder(home):
    """The real Documents folder (it may be redirected to OneDrive or another drive on Windows)."""
    if os.name == "nt":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:
                return Path(buffer.value)
        except Exception:
            pass
    return home / "Documents"


def _file_version(path):
    """'26.5' from an .exe's version resource (Windows only), or None."""
    try:
        import ctypes
        from ctypes import wintypes
        version = ctypes.WinDLL("version")
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            return None
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None
        fixed = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
        return f"{fixed[4] >> 16}.{fixed[4] & 0xFFFF}"          # dwProductVersionMS: major.minor
    except Exception:
        return None


class Machine:
    """Where this computer keeps each app's settings. Every root can be overridden (for tests)."""

    def __init__(self, system=None, home=None, documents=None, appdata=None, public=None,
                 program_files=None, applications=None, library=None, username=None):
        self.system = system or platform.system()
        self.windows = self.system == "Windows"
        self.home = Path(home) if home else Path.home()
        self.documents = Path(documents) if documents else (
            _documents_folder(self.home) if not home else self.home / "Documents")
        self.username = username or getpass.getuser()
        if self.windows:
            self.appdata = Path(appdata or os.environ.get("APPDATA") or self.home / "AppData" / "Roaming")
            self.public = Path(public or os.environ.get("PUBLIC") or r"C:\Users\Public")
            self.program_files = Path(program_files or os.environ.get("ProgramFiles") or r"C:\Program Files")
            self.library = None
        else:
            self.appdata = Path(appdata) if appdata else self.home / "Library"
            self.public = Path(public or "/Users/Shared")
            self.program_files = None
            self.library = Path(library or "/Library")
        self.applications = Path(applications or "/Applications")

    @property
    def platform_name(self):
        return "windows" if self.windows else "mac"

    @property
    def platform_label(self):
        return "Windows" if self.windows else "Mac"

    # After Effects ------------------------------------------------------------
    @property
    def ae_root(self):
        if self.windows:
            return self.appdata / "Adobe" / "After Effects"
        return self.appdata / "Preferences" / "Adobe" / "After Effects"

    @property
    def cep_extensions(self):
        if self.windows:
            return self.appdata / "Adobe" / "CEP" / "extensions"
        return self.appdata / "Application Support" / "Adobe" / "CEP" / "extensions"

    @property
    def mediacore(self):
        base = self.program_files / "Adobe" if self.windows else self.library / "Application Support" / "Adobe"
        return base / "Common" / "Plug-ins" / "7.0" / "MediaCore"

    # Premiere / Media Encoder -------------------------------------------------
    @property
    def premiere_root(self):
        return self.documents / "Adobe" / "Premiere Pro"

    @property
    def ame_root(self):
        return self.documents / "Adobe" / "Adobe Media Encoder"

    # Avid ----------------------------------------------------------------------
    @property
    def avid_root(self):
        if not self.windows:
            return self.public / "AvidMediaComposer"
        shared = self.public / "Documents" / "Avid Media Composer"
        personal = self.documents / "Avid Media Composer"
        return personal if (not shared.exists() and personal.exists()) else shared

    @property
    def backups(self):
        return self.documents / "AUTO AE Setup Backups"

    # installed apps -------------------------------------------------------------
    def installed_version(self, app):
        """Versions of an Adobe app installed here (from the app itself, so it works before first launch)."""
        names = {"ae": ("Adobe After Effects", "Support Files/AfterFX.exe"),
                 "premiere": ("Adobe Premiere Pro", "Adobe Premiere Pro.exe"),
                 "ame": ("Adobe Media Encoder", "Adobe Media Encoder.exe")}
        prefix, exe = names[app]
        found = []
        if self.windows:
            for folder in sorted((self.program_files / "Adobe").glob(prefix + " *")):
                if (folder / exe).is_file():
                    version = _file_version(folder / exe)
                    if version:
                        found.append(version)
        else:
            for folder in sorted(self.applications.glob(prefix + " *")):
                for bundle in folder.glob("*.app"):
                    try:
                        with open(bundle / "Contents" / "Info.plist", "rb") as handle:
                            version = plistlib.load(handle).get("CFBundleShortVersionString", "")
                    except (OSError, ValueError):
                        continue
                    parts = version.split(".")
                    if len(parts) >= 2 and parts[0].isdigit():
                        found.append(f"{parts[0]}.{parts[1]}")
        if app != "ae":                     # Premiere and Media Encoder keep settings per major version
            found = [f"{v.split('.')[0]}.0" for v in found]
        return found


def ae_year(version):
    """After Effects keeps presets in 'After Effects 2026' (version 26.x), 'After Effects 2021' (18.x)..."""
    major = version_key(version)[0]
    if major >= 22:
        return str(2000 + major)
    return {18: "2021", 17: "2020", 16: "CC 2019", 15: "CC 2018"}.get(major)


def premiere_profile(machine, version, create=False):
    """The profile folder inside a Premiere/AME version folder: Profile-<user>, or a synced profile."""
    base = machine.premiere_root / version
    own = base / f"Profile-{machine.username}"
    if own.is_dir():
        return own
    others = sorted(p for p in base.glob("Profile-*") if p.is_dir()) if base.is_dir() else []
    if others:
        return others[0]
    return own if create else None


def app_versions(machine, app):
    """[(version, opened)] newest first. `opened` means the app has made its settings folder."""
    opened = set()
    if app == "ae" and machine.ae_root.is_dir():
        for folder in machine.ae_root.iterdir():
            if re.fullmatch(r"\d+(\.\d+)+", folder.name) and \
                    (folder / f"Adobe After Effects {folder.name} Prefs.txt").is_file():
                opened.add(folder.name)
    elif app == "premiere" and machine.premiere_root.is_dir():
        for folder in machine.premiere_root.iterdir():
            if re.fullmatch(r"\d+\.0", folder.name) and any(folder.glob("Profile-*")):
                opened.add(folder.name)
    elif app == "ame" and machine.ame_root.is_dir():
        for folder in machine.ame_root.iterdir():
            if re.fullmatch(r"\d+\.0", folder.name):
                opened.add(folder.name)
    installed = set(machine.installed_version(app)) if app in ("ae", "premiere", "ame") else set()
    versions = sorted(opened | installed, key=version_key, reverse=True)
    return [(v, v in opened) for v in versions]


# --------------------------------------------------------------------------- what a kit holds
@dataclass
class Item:
    key: str
    app: str
    label: str
    hint: str
    kit_dir: str
    locate: object = None                   # (machine, version) -> Path on this machine
    include: object = None                  # (rel PurePosixPath, version) -> bool, for Capture
    default: bool = True
    manual: bool = False                    # filled with Add…, never captured
    per_platform: bool = False              # kit_dir/windows or kit_dir/mac
    admin: bool = False                     # the destination needs administrator rights
    add_types: tuple = ()                   # file dialog filters for Add…
    add_folders: bool = False
    add_subfolder: dict = field(default_factory=dict)


def _suffix(*suffixes):
    return lambda rel, version: rel.suffix.lower() in suffixes


def _ae_prefs(rel, version):
    return len(rel.parts) == 1 and rel.name.startswith(f"Adobe After Effects {version} Prefs") \
        and rel.suffix == ".txt"


def _pr_profile(machine, version):
    return premiere_profile(machine, version, create=True)


ITEMS = [
    Item("ae_shortcuts", "ae", "Keyboard shortcuts", "Your shortcut sets (Edit > Keyboard Shortcuts)",
         "after_effects/shortcuts", lambda m, v: m.ae_root / v / "aeks", _suffix(".txt"),
         add_types=(("Shortcut sets", "*.txt"),)),
    Item("ae_workspaces", "ae", "Workspaces", "Saved and modified panel layouts",
         "after_effects/workspaces", lambda m, v: m.ae_root / v,
         lambda rel, v: len(rel.parts) > 1 and rel.parts[0] in ("ModifiedWorkspaces", "OriginalUserWorkspaces")),
    Item("ae_scripts", "ae", "Scripts & panels", "File > Scripts and Window > ScriptUI panels (.jsx, .jsxbin)",
         "after_effects/scripts", lambda m, v: m.ae_root / v / "Scripts",
         add_types=(("Scripts", "*.jsx *.jsxbin"),), add_subfolder={"panel": "ScriptUI Panels"}),
    Item("ae_presets", "ae", "Animation presets", "User Presets (.ffx)", "after_effects/presets",
         lambda m, v: (m.documents / "Adobe" / f"After Effects {ae_year(v)}" / "User Presets") if ae_year(v) else None,
         add_types=(("Animation presets", "*.ffx"),)),
    Item("ae_prefs", "ae", "Preferences", "Everything in Edit > Preferences. Holds this machine's disk cache "
         "and memory settings too, so leave it off unless the machines match", "after_effects/preferences",
         lambda m, v: m.ae_root / v, _ae_prefs, default=False),
    Item("ae_plugins", "ae", "Plug-ins", "Loose .aex / .plugin files. Licensed plug-ins need their "
         "installer instead (add it under Installers)", "after_effects/plugins", lambda m, v: m.mediacore,
         manual=True, per_platform=True, admin=True, add_folders=True,
         add_types=(("Plug-ins", "*.aex *.plugin *.prm"),)),
    Item("ae_extensions", "ae", "Extensions", "Panels under Window > Extensions (.zxp or an extension folder)",
         "after_effects/extensions", lambda m, v: m.cep_extensions, manual=True, add_folders=True,
         add_types=(("Extensions", "*.zxp"),)),

    Item("pr_shortcuts", "premiere", "Keyboard shortcuts", "Your .kys shortcut sets",
         "premiere_pro/shortcuts", lambda m, v: _pr_profile(m, v) / ("Win" if m.windows else "Mac"),
         _suffix(".kys"), add_types=(("Premiere shortcuts", "*.kys"),)),
    Item("pr_workspaces", "premiere", "Workspaces", "Your panel layouts (Window > Workspaces)",
         "premiere_pro/workspaces", _pr_profile,
         lambda rel, v: (len(rel.parts) > 1 and rel.parts[0] == "Layouts") or str(rel) == "LayoutsWorkspaceConfig.xml"),
    Item("pr_effect_presets", "premiere", "Effect presets", "Effect presets and custom bins in the Effects panel",
         "premiere_pro/effect_presets", _pr_profile,
         lambda rel, v: str(rel) == "Effect Presets and Custom Items.prfpset"),
    Item("pr_prefs", "premiere", "Preferences", "Everything in Edit > Preferences, including recent projects",
         "premiere_pro/preferences", _pr_profile, lambda rel, v: str(rel) == "Adobe Premiere Pro Prefs",
         default=False),

    Item("ame_presets", "ame", "Export presets", "Your .epr presets (also used by Premiere's Export)",
         "media_encoder/presets", lambda m, v: m.ame_root / v / "Presets", _suffix(".epr"),
         add_types=(("Export presets", "*.epr"),)),

    Item("avid_users", "avid", "User profiles", "Keyboard, workspaces, bin views and settings for each user",
         "avid/users", lambda m, v: m.avid_root / "Avid Users",
         lambda rel, v: len(rel.parts) > 1 and rel.parts[0] not in AVID_NOT_USERS, add_folders=True),
    Item("avid_site", "avid", "Site settings", "Settings shared by every user on the machine",
         "avid/site_settings", lambda m, v: m.avid_root / "Settings", lambda rel, v: len(rel.parts) == 1,
         default=False),

    Item("installers", "installers", "Installers", "Plug-in suites, codecs, fonts managers... Run one after "
         "another in name order, before the settings are copied", "installers", manual=True,
         per_platform=True, add_types=(("Installers", "*.exe *.msi *.pkg *.dmg"),)),
]
ITEM_BY_KEY = {item.key: item for item in ITEMS}
APPS = [("ae", "After Effects"), ("premiere", "Premiere Pro"), ("ame", "Media Encoder"),
        ("avid", "Avid Media Composer"), ("installers", "Installers")]
APP_NAMES = dict(APPS)
PROCESSES = {"ae": ("afterfx", "after effects"), "premiere": ("adobe premiere pro",),
             "ame": ("adobe media encoder",), "avid": ("avidmediacomposer",)}


# --------------------------------------------------------------------------- files
def list_files(root, include=None, version=None):
    """Files under `root` as relative POSIX paths, sorted. Skips OS junk like .DS_Store."""
    root = Path(root)
    if not root.is_dir():
        return []
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in JUNK)
        for name in files:
            if name in JUNK or name.startswith("._"):
                continue
            rel = PurePosixPath(Path(folder, name).relative_to(root).as_posix())
            if include is None or include(rel, version):
                found.append(rel)
    return sorted(found)


def kit_item_dir(kit, item, machine):
    path = Path(kit) / item.kit_dir
    return path / machine.platform_name if item.per_platform else path


def kit_entries(kit, item, machine):
    """What the kit has for `item`: files for settings, top-level entries for plug-ins/installers."""
    folder = kit_item_dir(kit, item, machine)
    if item.manual:
        if not folder.is_dir():
            return []
        return sorted((p for p in folder.iterdir() if p.name not in JUNK and not p.name.startswith("._")),
                      key=lambda p: p.name.lower())
    return list_files(folder)


def to_kit_name(item, rel, version):
    if item.key == "ae_prefs":
        return PurePosixPath(rel.as_posix().replace(f"Adobe After Effects {version} ",
                                                    f"Adobe After Effects {VERSION_TOKEN} ", 1))
    return rel


def from_kit_name(item, rel, version):
    if item.key == "ae_prefs":
        return PurePosixPath(rel.as_posix().replace(VERSION_TOKEN, version, 1))
    return rel


def read_manifest(kit):
    try:
        with open(Path(kit) / MANIFEST, encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_manifest(kit, data):
    Path(kit).mkdir(parents=True, exist_ok=True)
    with open(Path(kit) / MANIFEST, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


# active shortcut set --------------------------------------------------------
AE_SHORTCUT_PREF = re.compile(r'^(\s*"Shortcut File Location"\s*=\s*")([^"\r\n]*)(")', re.M)
PR_SHORTCUT_PREF = re.compile(r"(<FE\.Prefs\.Shortcuts\.Filename>)(.*?)(</FE\.Prefs\.Shortcuts\.Filename>)")


def _prefs_file(item_key, machine, version):
    if item_key == "ae_shortcuts":
        return machine.ae_root / version / f"Adobe After Effects {version} Prefs.txt", AE_SHORTCUT_PREF
    profile = premiere_profile(machine, version)
    return (profile / "Adobe Premiere Pro Prefs" if profile else None), PR_SHORTCUT_PREF


def read_active_shortcuts(item_key, machine, version):
    path, pattern = _prefs_file(item_key, machine, version)
    try:
        match = pattern.search(path.read_text(encoding="utf-8", errors="surrogateescape"))
    except (OSError, AttributeError):
        return None
    return match.group(2) if match else None


def write_active_shortcuts(item_key, machine, version, name):
    """Point the app's prefs at shortcut set `name`. Returns the prefs path, or None if not possible."""
    path, pattern = _prefs_file(item_key, machine, version)
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if not pattern.search(text):
        return None
    text = pattern.sub(lambda m: m.group(1) + name + m.group(3), text, count=1)
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
        handle.write(text)
    return path


# --------------------------------------------------------------------------- capture / deploy
def capture(kit, keys, machine, versions, log):
    """Copy the chosen settings from this machine into the kit. Returns the number of files copied."""
    kit = Path(kit)
    manifest = read_manifest(kit)
    manifest.setdefault("format", 1)
    active = manifest.setdefault("active_shortcuts", {})
    total = 0
    for item in ITEMS:
        if item.key not in keys or item.manual:
            continue
        version = versions.get(item.app)
        title = f"{APP_NAMES[item.app]} {item.label.lower()}"
        source = item.locate(machine, version) if (version or item.app == "avid") else None
        files = list_files(source, item.include, version) if source else []
        if not files:
            log(f"Skipped: {title}, nothing found on this machine")
            continue
        target = kit_item_dir(kit, item, machine)
        if target.exists():
            shutil.rmtree(target)
        for rel in files:
            destination = target / to_kit_name(item, rel, version)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, destination)
        total += len(files)
        log(f"Copied: {title}, {len(files)} file{'s' if len(files) != 1 else ''} from {source}")
        if item.key in ("ae_shortcuts", "pr_shortcuts"):
            current = read_active_shortcuts(item.key, machine, version)
            if current:
                active[item.key] = current
                log(f"Found: active shortcut set is “{current}”")
    manifest["captured"] = {"when": datetime.now().isoformat(timespec="seconds"),
                            "machine": platform.node(), "system": machine.platform_label,
                            "versions": {app: v for app, v in versions.items() if v}}
    write_manifest(kit, manifest)
    return total


class Backup:
    """Copies files that are about to be overwritten into Documents/AUTO AE Setup Backups/<time>/."""

    def __init__(self, machine):
        self.root = machine.backups / datetime.now().strftime("%Y-%m-%d %H%M%S")
        self.count = 0

    def save(self, path, key):
        path = Path(path)
        if not path.exists():
            return
        anchor = Path(path.anchor)
        destination = self.root / key / path.relative_to(anchor)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(path, destination)
        self.count += 1


def _copy_settings(item, kit, machine, version, backup, log):
    files = kit_entries(kit, item, machine)
    title = f"{APP_NAMES[item.app]} {item.label.lower()}"
    target = item.locate(machine, version)
    if target is None:
        log(f"Skipped: {title}, no place for it on this machine")
        return 0
    source = kit_item_dir(kit, item, machine)
    copied = same = 0
    for rel in files:
        destination = target / from_kit_name(item, rel, version)
        if destination.is_file() and filecmp.cmp(source / rel, destination, shallow=False):
            same += 1
            continue
        backup.save(destination, item.key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, destination)
        copied += 1
    note = f", {same} already the same" if same else ""
    log(f"Copied: {title}, {copied} file{'s' if copied != 1 else ''} to {target}{note}")
    return copied


def _set_active_shortcuts(item, kit, machine, version, log):
    name = read_manifest(kit).get("active_shortcuts", {}).get(item.key)
    if not name:
        return
    folder = item.locate(machine, version)
    if not (folder / name).is_file():
        return
    try:
        changed = write_active_shortcuts(item.key, machine, version, name)
    except OSError as exc:
        log(f"Warning: couldn't switch to “{name}”: {error_help.short_reason(exc)}")
        return
    if changed:
        log(f"Done: “{name}” is now the active shortcut set")
    else:
        where = "Edit > Keyboard Shortcuts"
        log(f"Warning: open {APP_NAMES[item.app]} once, then choose “{name}” in {where}")


def _install_extensions(item, kit, machine, backup, log):
    target = machine.cep_extensions
    count = 0
    for entry in kit_entries(kit, item, machine):
        if entry.is_dir():
            destination = target / entry.name
            backup.save(destination, item.key)
            shutil.copytree(entry, destination, dirs_exist_ok=True)
        elif entry.suffix.lower() == ".zxp":
            destination = target / entry.stem
            backup.save(destination, item.key)
            safe_extract(entry, destination)
        else:
            log(f"Skipped: {entry.name}, extensions must be a .zxp or a folder")
            continue
        count += 1
        log(f"Copied: extension {entry.name} to {destination}")
    return count


def safe_extract(archive, destination):
    """Unzip, refusing entries that would land outside `destination`."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError(f"{Path(archive).name} has an unsafe path: {member.filename}")
        destination.mkdir(parents=True, exist_ok=True)
        bundle.extractall(destination)


def _install_plugins(item, kit, machine, log, elevate):
    target = machine.mediacore
    entries = kit_entries(kit, item, machine)
    blocked = []
    for entry in entries:
        destination = target / entry.name
        try:
            target.mkdir(parents=True, exist_ok=True)
            if entry.is_dir():
                shutil.copytree(entry, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, destination)
            log(f"Copied: plug-in {entry.name} to {target}")
        except PermissionError:
            blocked.append(entry)
    if blocked:
        log(f"Started: asking for administrator rights to copy {len(blocked)} plug-in(s) into {target}")
        error = elevate([(entry, target) for entry in blocked])
        if error:
            log(f"Failed: plug-ins weren't installed: {error}")
        else:
            for entry in blocked:
                log(f"Copied: plug-in {entry.name} to {target}")
    return len(entries)


def run_installers(item, kit, machine, log, launch):
    entries = kit_entries(kit, item, machine)
    for n, entry in enumerate(entries, start=1):
        log(f"[{n}/{len(entries)}] Started: {entry.name}. Finish it to move on to the next one")
        try:
            code = launch(entry)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1223:
                log(f"[{n}/{len(entries)}] Skipped: {entry.name}, cancelled at the administrator prompt")
            else:
                log(f"[{n}/{len(entries)}] Failed: {entry.name}: {error_help.short_reason(exc, entry)}")
            continue
        if code not in (None, 0, 3010):      # 3010: done, restart needed
            log(f"[{n}/{len(entries)}] Warning: {entry.name} closed with code {code}")
        else:
            log(f"[{n}/{len(entries)}] Finished: {entry.name}")
    return len(entries)


def deploy(kit, keys, machine, versions, log, elevate=None, launch=None):
    """Put the chosen parts of the kit onto this machine. Installers run first, then plug-ins,
    then settings. Returns the Backup (its .count says how many things were backed up)."""
    backup = Backup(machine)
    prefs = ["ae_prefs", "pr_prefs"]           # before the shortcuts, which then switch the active set
    order = ["installers", "ae_plugins", "ae_extensions"] + prefs +         [i.key for i in ITEMS if not i.manual and i.key not in prefs]
    for key in order:
        item = ITEM_BY_KEY[key]
        if key not in keys or not kit_entries(kit, item, machine):
            continue
        version = versions.get(item.app)
        if item.app in ("ae", "premiere", "ame") and not version and not item.manual:
            log(f"Skipped: {APP_NAMES[item.app]} {item.label.lower()}, {APP_NAMES[item.app]} isn't installed here")
            continue
        try:
            if key == "installers":
                run_installers(item, kit, machine, log, launch or launch_installer)
            elif key == "ae_plugins":
                _install_plugins(item, kit, machine, log, elevate or elevated_copy)
            elif key == "ae_extensions":
                _install_extensions(item, kit, machine, backup, log)
            else:
                _copy_settings(item, kit, machine, version, backup, log)
                if key in ("ae_shortcuts", "pr_shortcuts"):
                    _set_active_shortcuts(item, kit, machine, version, log)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            log(f"Failed: {APP_NAMES[item.app]} {item.label.lower()}: "
                f"{error_help.short_reason(exc, getattr(exc, 'filename', None))}")
    return backup


# --------------------------------------------------------------------------- OS helpers
def running_apps(apps):
    """Names of the apps in `apps` that are open right now."""
    try:
        if os.name == "nt":
            output = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15).stdout
            names = [line.split('","')[0].strip('"').lower() for line in output.splitlines() if line]
        else:
            output = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=15).stdout
            names = [os.path.basename(line.strip()).lower() for line in output.splitlines()]
    except (OSError, subprocess.SubprocessError):
        return []
    return [APP_NAMES[app] for app in apps
            if any(name.startswith(prefix) for name in names for prefix in PROCESSES.get(app, ()))]


def _shell_execute(file, params=None, verb="open", show=1):
    """Run through the Windows shell (so UAC prompts work) and wait. Returns the exit code or None."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.windll.ole32.CoInitializeEx(None, 0x2 | 0x4)

    info = ShellExecuteInfo(cbSize=ctypes.sizeof(ShellExecuteInfo), fMask=0x40 | 0x100,   # NOCLOSEPROCESS | NOASYNC
                            lpVerb=verb, lpFile=str(file), lpParameters=params,
                            lpDirectory=str(Path(file).parent) if Path(file).is_absolute() else None, nShow=show)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        raise OSError(None, ctypes.FormatError(error), str(file), error)
    if not info.hProcess:
        return None
    kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return code.value


def launch_installer(path):
    if os.name == "nt":
        return _shell_execute(path)
    return subprocess.run(["open", "-W", str(path)]).returncode


def elevated_copy(pairs):
    """Copy [(source, destination folder)] with administrator rights. Returns an error message or None."""
    work = Path(tempfile.mkdtemp(prefix="autoae-setup-"))
    try:
        if os.name == "nt":
            def q(value):
                return "'" + str(value).replace("'", "''") + "'"
            result = work / "result.txt"
            lines = ["$ErrorActionPreference = 'Stop'", "try {"]
            for source, folder in pairs:
                lines.append(f"  New-Item -ItemType Directory -Force -Path {q(folder)} | Out-Null")
                lines.append(f"  Copy-Item -LiteralPath {q(source)} -Destination {q(folder)} -Recurse -Force")
            lines += [f"  Set-Content -LiteralPath {q(result)} -Value 'ok'", "} catch {",
                      f"  Set-Content -LiteralPath {q(result)} -Value $_.Exception.Message", "}"]
            script = work / "copy.ps1"
            script.write_text("\r\n".join(lines), encoding="utf-8-sig")
            try:
                _shell_execute("powershell.exe", f'-NoProfile -ExecutionPolicy Bypass -File "{script}"',
                               verb="runas", show=0)
            except OSError as exc:
                return "cancelled at the administrator prompt" if exc.winerror == 1223 else str(exc)
            outcome = result.read_text(encoding="utf-8-sig").strip() if result.exists() else "nothing was copied"
            return None if outcome == "ok" else outcome
        commands = " && ".join(f"mkdir -p {shlex.quote(str(folder))} && "
                               f"ditto {shlex.quote(str(source))} {shlex.quote(str(Path(folder) / Path(source).name))}"
                               for source, folder in pairs)
        applescript = 'do shell script "{}" with administrator privileges'.format(
            commands.replace("\\", "\\\\").replace('"', '\\"'))
        done = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)
        return None if done.returncode == 0 else (done.stderr.strip() or "cancelled")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def open_path(path):
    try:
        if os.name == "nt":
            os.startfile(str(path))
        else:
            subprocess.Popen(["open", str(path)])
    except OSError:
        pass


# --------------------------------------------------------------------------- the tool
class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "Setup Kit"
        self.description = "Carry your shortcuts, workspaces, presets and plug-ins to any machine"
        self.category = "Project Setup"
        self.icon = "keyboard"
        self.machine = Machine()
        self.rows = {}
        self.version_vars = {}
        self.version_choices = {}
        self.busy = False
        self.messages = queue.Queue()

    # ----------------------------------------------------------------- window
    def create_window(self, parent):
        window = self.create_tool_window(parent, "Setup Kit", 1000, 900)
        self.window = window
        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 14))

        actions = theme.action_bar(page)
        self.status = theme.StatusLabel(actions, text="", font=theme.font("body"),
                                        rules=[("Done", "success"), ("Failed", "danger"), ("Working", "accent_text")])
        self.status.pack(side="left", fill="x", expand=True)
        self.deploy_button = theme.Button(actions, "Deploy to This Machine", kind="primary", width=190,
                                          command=self.start_deploy)
        self.deploy_button.pack(side="right")
        self.capture_button = theme.Button(actions, "Capture from This Machine", kind="secondary", width=200,
                                           command=self.start_capture)
        self.capture_button.pack(side="right", padx=(0, 8))

        # --- the kit ------------------------------------------------------------
        kit_card = theme.Card(page, title="Kit",
                              subtitle="A folder on a USB drive, Dropbox or NAS. Capture fills it; Deploy "
                                       "puts it onto whatever machine you're at.")
        kit_card.pack(fill="x")
        field_ = theme.PathField(kit_card.body, "Kit folder", "Choose or create a folder for your kit",
                                 command=self.browse_kit, label_width=80)
        field_.pack(fill="x")
        self.kit_entry = field_.entry
        self.kit_entry.bind("<Return>", lambda e: self.kit_changed())
        self.kit_entry.bind("<FocusOut>", lambda e: self.kit_changed())
        row = ctk.CTkFrame(kit_card.body, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        self.kit_summary = theme.muted(row, "", role="body")
        self.kit_summary.pack(side="left", fill="x", expand=True)
        theme.Button(row, "Show Folder", kind="ghost", width=100, command=self.show_kit).pack(side="right")
        theme.Button(row, "Export .zip…", kind="secondary", width=110,
                     command=self.export_zip).pack(side="right", padx=(0, 6))
        theme.Button(row, "Open .zip…", kind="secondary", width=100,
                     command=self.import_zip).pack(side="right", padx=(0, 6))

        # --- contents -------------------------------------------------------------
        items_card = theme.Card(page, title="What to capture and deploy",
                                subtitle="Ticked items are captured from this machine and deployed to it. "
                                         "Items marked “added by hand” come from Add… only.")
        items_card.pack(fill="both", expand=True, pady=(12, 0))
        self.items_frame = ctk.CTkScrollableFrame(items_card.body, fg_color="transparent", height=330)
        self.items_frame.pack(fill="both", expand=True)
        self.items_frame.grid_columnconfigure(1, weight=1)
        self.build_rows(self.items_frame)

        log_card = theme.Card(page, title="Log")
        items_card.pack_forget()                        # the scrolling list gives way when space is short
        log_card.pack(side="bottom", fill="x", pady=(12, 0))
        items_card.pack(fill="both", expand=True, pady=(12, 0))
        self.log = theme.LogBox(log_card.body, height=130,
                                empty_text="Capture or deploy to see what was copied.")
        self.log.pack(fill="x")

        kit = self.load_settings().get("kit", "")
        if kit:
            self.kit_entry.insert(0, kit)
        self.refresh()
        return window

    def build_rows(self, master):
        grid_row = 0
        for app, app_name in APPS:
            header = ctk.CTkFrame(master, fg_color="transparent")
            header.grid(row=grid_row, column=0, columnspan=4, sticky="ew", pady=(14 if grid_row else 2, 4))
            ctk.CTkLabel(header, text=app_name, font=theme.font("heading"), anchor="w",
                         height=22).pack(side="left")
            if app in ("ae", "premiere", "ame"):
                choices = app_versions(self.machine, app)
                labels = [v if opened else f"{v} (not opened yet)" for v, opened in choices]
                self.version_choices[app] = dict(zip(labels, (v for v, _ in choices)))
                var = tk.StringVar(value=labels[0] if labels else "Not installed")
                self.version_vars[app] = var
                menu = ctk.CTkOptionMenu(header, variable=var, values=labels or ["Not installed"], width=190,
                                         height=26, command=lambda _: self.refresh())
                menu.pack(side="right")
                if not labels:
                    menu.configure(state="disabled")
                theme.muted(header, "Version on this machine", role="caption").pack(side="right", padx=(0, 8))
            grid_row += 1
            for item in (i for i in ITEMS if i.app == app):
                self.build_row(master, item, grid_row)
                grid_row += 1

    def build_row(self, master, item, grid_row):
        var = tk.BooleanVar(value=item.default)
        box = ctk.CTkCheckBox(master, text="", variable=var, width=24, checkbox_width=18, checkbox_height=18)
        box.grid(row=grid_row, column=0, sticky="nw", padx=(4, 6), pady=(6, 0))
        text = ctk.CTkFrame(master, fg_color="transparent")
        text.grid(row=grid_row, column=1, sticky="ew", pady=3)
        title = item.label + ("  ·  added by hand" if item.manual else "") + \
            ("  ·  needs admin" if item.admin else "")
        ctk.CTkLabel(text, text=title, font=theme.font("body_strong"), anchor="w", height=20).pack(fill="x")
        theme.muted(text, item.hint, height=16, wraplength=560).pack(fill="x")
        status = theme.muted(text, "", height=16)
        status.pack(fill="x")
        buttons = ctk.CTkFrame(master, fg_color="transparent")
        buttons.grid(row=grid_row, column=2, sticky="ne", pady=3)
        if item.add_types or item.add_folders:
            theme.Button(buttons, "Add…", kind="secondary", width=64, height=28,
                         command=lambda i=item: self.add_to_kit(i)).pack(side="left")
        theme.Button(buttons, "Show", kind="ghost", width=56, height=28,
                     command=lambda i=item: self.show_item(i)).pack(side="left", padx=(4, 0))
        self.rows[item.key] = {"var": var, "status": status}

    # ----------------------------------------------------------------- state
    def kit(self):
        return self.kit_entry.get().strip()

    def versions(self):
        result = {}
        for app, var in self.version_vars.items():
            result[app] = self.version_choices.get(app, {}).get(var.get())
        return result

    def selected(self):
        return {key for key, row in self.rows.items() if row["var"].get()}

    def kit_changed(self):
        self.save_settings()
        self.refresh()

    def refresh(self):
        """Update the kit summary and every row's 'kit has / this machine has' line."""
        if not self.window.winfo_exists():
            return
        kit, versions = self.kit(), self.versions()
        manifest = read_manifest(kit) if kit else {}
        captured = manifest.get("captured")
        if not kit:
            self.kit_summary.configure(text="No kit chosen yet.")
        elif not Path(kit).is_dir():
            self.kit_summary.configure(text="This folder doesn't exist yet. Capture will create it.")
        elif captured:
            when = captured.get("when", "").replace("T", " ")[:16]
            self.kit_summary.configure(text=f"Captured {when} on {captured.get('machine', '?')} "
                                            f"({captured.get('system', '?')}).")
        else:
            self.kit_summary.configure(text="Nothing captured into this kit yet.")
        for item in ITEMS:
            parts = []
            if kit:
                entries = kit_entries(kit, item, self.machine)
                noun = "item" if item.manual else "file"
                count = f"{len(entries)} {noun}{'s' if len(entries) != 1 else ''}"
                parts.append(f"Kit: {count}" + (f" for {self.machine.platform_label}" if item.per_platform else ""))
            parts.append(self.machine_status(item, versions.get(item.app)))
            self.rows[item.key]["status"].configure(text="  ·  ".join(parts))

    def machine_status(self, item, version):
        if item.key == "installers":
            return "Runs each one and waits for it to finish"
        if item.app in ("ae", "premiere", "ame") and not version:
            return f"{APP_NAMES[item.app]} isn't installed here"
        location = item.locate(self.machine, version)
        if location is None:
            return "No place for it on this machine"
        if item.manual:
            return f"Installs to {location}"
        count = len(list_files(location, item.include, version))
        return f"This machine: {count} file{'s' if count != 1 else ''}"

    def load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
                json.dump({"kit": self.kit()}, handle, indent=2)
        except OSError:
            pass

    # ----------------------------------------------------------------- kit folder
    def set_kit(self, path):
        self.kit_entry.delete(0, tk.END)
        self.kit_entry.insert(0, str(path))
        self.kit_changed()

    def browse_kit(self):
        folder = filedialog.askdirectory(title="Choose or create a kit folder", parent=self.window)
        if folder:
            self.set_kit(os.path.normpath(folder))

    def need_kit(self, create=False):
        kit = self.kit()
        if not kit:
            messagebox.showerror("Setup Kit", "Choose a kit folder first.", parent=self.window)
            return None
        if not Path(kit).is_dir():
            if not create:
                messagebox.showerror("Setup Kit", f"The kit folder isn't there:\n{kit}", parent=self.window)
                return None
            Path(kit).mkdir(parents=True, exist_ok=True)
        return Path(kit)

    def show_kit(self):
        kit = self.need_kit(create=True)
        if kit:
            open_path(kit)

    def show_item(self, item):
        kit = self.need_kit(create=True)
        if kit:
            folder = kit_item_dir(kit, item, self.machine)
            folder.mkdir(parents=True, exist_ok=True)
            open_path(folder)
            self.refresh()

    def add_to_kit(self, item):
        kit = self.need_kit(create=True)
        if not kit:
            return
        paths = []
        if item.add_types:
            types = list(item.add_types) + [("All files", "*.*")]
            paths = list(filedialog.askopenfilenames(title=f"Add to {item.label.lower()}", filetypes=types,
                                                     parent=self.window))
        if not paths and item.add_folders:
            folder = filedialog.askdirectory(title=f"Add a folder to {item.label.lower()}", parent=self.window)
            paths = [folder] if folder else []
        if not paths:
            return
        target = kit_item_dir(kit, item, self.machine)
        if "panel" in item.add_subfolder and messagebox.askyesno(
                "Setup Kit", "Are these dockable panels (opened from the Window menu)?\n\n"
                             "No puts them in File > Scripts.", parent=self.window):
            target = target / item.add_subfolder["panel"]
        target.mkdir(parents=True, exist_ok=True)
        try:
            for path in paths:
                destination = target / os.path.basename(os.path.normpath(path))
                if os.path.isdir(path):
                    shutil.copytree(path, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(path, destination)
                self.log.insert(tk.END, f"Copied: {os.path.basename(path)} into the kit ({item.label})")
        except OSError as exc:
            messagebox.showerror("Setup Kit", error_help.explain(exc, action="add it to the kit"),
                                 parent=self.window)
        self.refresh()

    def export_zip(self):
        kit = self.need_kit()
        if not kit:
            return
        target = filedialog.asksaveasfilename(title="Save the kit as a .zip", defaultextension=".zip",
                                              initialfile=kit.name + ".zip", filetypes=[("Zip", "*.zip")],
                                              parent=self.window)
        if not target:
            return
        if Path(target).resolve().is_relative_to(kit.resolve()):
            messagebox.showerror("Setup Kit", "Save the .zip outside the kit folder.", parent=self.window)
            return
        try:
            archive = shutil.make_archive(os.path.splitext(target)[0], "zip", root_dir=kit)
        except OSError as exc:
            messagebox.showerror("Setup Kit", error_help.explain(exc, target, "make the .zip"), parent=self.window)
            return
        self.log.insert(tk.END, f"Created: {archive}")
        self.status.configure(text="Done: kit saved as a .zip.")

    def import_zip(self):
        archive = filedialog.askopenfilename(title="Open a kit .zip", filetypes=[("Zip", "*.zip")],
                                             parent=self.window)
        if not archive:
            return
        destination = self.machine.documents / "AUTO AE Setup Kit" / Path(archive).stem
        if destination.exists() and any(destination.iterdir()):
            if not messagebox.askyesno("Setup Kit", f"Replace the kit already unpacked at\n{destination}?",
                                       parent=self.window):
                return
            shutil.rmtree(destination, ignore_errors=True)
        try:
            safe_extract(archive, destination)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            messagebox.showerror("Setup Kit", f"Couldn't open the .zip.\n\n{exc}", parent=self.window)
            return
        inner = [p for p in destination.iterdir() if p.name not in JUNK]
        if not (destination / MANIFEST).exists() and len(inner) == 1 and (inner[0] / MANIFEST).exists():
            destination = inner[0]                        # zipped with the kit folder inside
        self.log.insert(tk.END, f"Created: unpacked the kit to {destination}")
        self.set_kit(destination)

    # ----------------------------------------------------------------- capture / deploy
    def start_capture(self):
        kit = self.need_kit(create=True)
        keys = self.selected()
        if not kit or not keys:
            return
        manifest = read_manifest(kit)
        if manifest.get("captured") and not messagebox.askyesno(
                "Setup Kit", "Replace the ticked items in this kit with the settings from this machine?\n\n"
                             "Plug-ins, extensions and installers you added by hand are kept.",
                parent=self.window):
            return
        versions = self.versions()
        self.run_in_background("Capturing",
                               lambda log: capture(kit, keys, self.machine, versions, log),
                               lambda total: f"Done: captured {total} files into the kit.")

    def start_deploy(self):
        kit = self.need_kit()
        keys = self.selected()
        if not kit or not keys:
            return
        todo = [ITEM_BY_KEY[key] for key in keys if kit_entries(kit, ITEM_BY_KEY[key], self.machine)]
        if not todo:
            messagebox.showinfo("Setup Kit", "The kit has nothing for the ticked items yet.", parent=self.window)
            return
        busy = running_apps({item.app for item in todo if not item.manual or item.key == "ae_plugins"})
        if busy:
            messagebox.showerror("Setup Kit", "Close these first, or they'll overwrite the new settings when "
                                              "they quit:\n\n  • " + "\n  • ".join(busy), parent=self.window)
            return
        lines = [f"  • {APP_NAMES[i.app]}: {i.label}" for i in ITEMS if i in todo]
        extra = []
        if any(i.key == "installers" for i in todo):
            extra.append("Installers open one at a time. Finish each one to move on.")
        if any(i.admin for i in todo):
            extra.append("Plug-ins need administrator rights; you'll be asked once.")
        extra.append(f"Anything replaced is backed up to\n{self.machine.backups}")
        if not messagebox.askyesno("Setup Kit", "Deploy to this machine:\n\n" + "\n".join(lines) + "\n\n" +
                                   "\n".join(extra), parent=self.window):
            return
        versions = self.versions()
        self.run_in_background("Deploying",
                               lambda log: deploy(kit, keys, self.machine, versions, log),
                               self.deploy_summary)

    def deploy_summary(self, backup):
        if backup.count:
            self.log.insert(tk.END, f"Backup completed: {backup.count} item(s) saved to {backup.root}")
        return "Done: deployed. Restart any open Adobe or Avid apps to see the changes."

    def run_in_background(self, verb, work, summary):
        if self.busy:
            return
        self.busy = True
        self.capture_button.configure(state="disabled")
        self.deploy_button.configure(state="disabled")
        self.status.configure(text=f"Working: {verb.lower()}…")
        self.log.insert(tk.END, f"[{datetime.now():%H:%M:%S}] Starting: {verb.lower()}")

        def worker():
            try:
                result = work(lambda text: self.messages.put(("log", text)))
                self.messages.put(("done", lambda: summary(result)))     # summary runs on the UI thread
            except Exception as exc:                 # report anything unexpected instead of dying silently
                message = f"Failed: {error_help.short_reason(exc)}"
                self.messages.put(("done", lambda: message))

        threading.Thread(target=worker, daemon=True).start()
        self.window.after(100, self.pump)

    def pump(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self.log.insert(tk.END, payload)
                    continue
                payload = payload()
                self.busy = False
                self.capture_button.configure(state="normal")
                self.deploy_button.configure(state="normal")
                self.status.configure(text=payload)
                self.log.insert(tk.END, payload)
                self.refresh()
                return
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            self.window.after(100, self.pump)
