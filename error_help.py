"""Plain-English explanations for file and disk errors.

Python's own messages ("[Errno 22] Invalid argument") say what the operating system returned, not
what went wrong or which file it was about. `explain()` turns an exception into a message that
names the file, the step that failed, the likely cause and what to try. `install()` (called once
by main.py) adds the same kind of hint to every error dialog in the app, so older tools that
still show `str(exc)` get it too.
"""
import errno
import os
import re
import sys
import tkinter.messagebox as messagebox

WINDOWS_BAD_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
MARKER = "\u2014 What this means"      # present once a message has been explained

# Windows error codes (exc.winerror). Many of these reach Python as a bare "[Errno 22]"
# because the C runtime maps codes it doesn't know to EINVAL.
WINERROR_HINTS = {
    2: "The file isn't there any more. It may have been moved, renamed or deleted.",
    3: "The folder isn't there. The drive may have been unplugged or the folder renamed.",
    5: "Access was denied. The file may be read-only, open in another app, or in a protected folder.",
    19: "The drive is write-protected. Check the lock switch on the card or drive.",
    21: "The drive isn't ready. It may have been ejected or gone to sleep.",
    23: "The drive couldn't read this part of the file (a data/CRC error). The card or drive may have a "
        "bad spot, or the card reader or cable lost contact.",
    32: "Another program has the file open. Close it (Premiere, Resolve, Avid, a media player) and try again.",
    33: "Another program has locked part of the file. Close anything using it and try again.",
    39: "The destination drive is full.",
    53: "The network location couldn't be found.",
    59: "The network connection dropped while the file was being used.",
    64: "The network connection dropped while the file was being used.",
    87: "Windows rejected the request. On a card or external drive this usually means it disconnected "
        "or can't read this part of the file.",
    112: "The destination drive is full.",
    121: "The drive stopped responding (timeout). A loose cable, a flaky card reader or a sleeping network "
         "drive is the usual cause.",
    123: "The file or folder name has characters Windows doesn't allow.",
    206: "The full path is too long for Windows. Use a shorter destination folder or turn on long paths.",
    1117: "The drive reported a hardware read/write error. The card, drive or cable may be failing.",
    1167: "The drive was disconnected.",
    1392: "The file is corrupted on disk and can't be read.",
}

ERRNO_HINTS = {
    errno.ENOENT: "The file or folder isn't there. It may have been moved, renamed, or the drive unplugged.",
    errno.EACCES: "Access was denied. The file may be read-only, open in another app, or in a protected folder.",
    errno.EPERM: "The operating system doesn't allow this here. The file may be locked or read-only.",
    errno.ENOSPC: "The destination drive is full.",
    errno.EIO: "The drive had a read/write error. The card, drive or cable may be failing.",
    errno.ENAMETOOLONG: "The name or path is too long. Use a shorter destination folder.",
    errno.EROFS: "The drive is read-only.",
    errno.EFBIG: "The file is too big for the destination drive's format (FAT32 can't hold files of 4 GB or more).",
    errno.EEXIST: "Something with that name already exists there.",
    errno.EISDIR: "A folder was found where a file was expected.",
    errno.ENOTDIR: "A file was found where a folder was expected.",
    errno.EINVAL: "The operating system rejected the request. For file copies this usually means one of:\n"
                  "  \u2022 the card or drive disconnected or couldn't read part of the file (bad spot, loose "
                  "cable, flaky card reader),\n"
                  "  \u2022 the file name has characters the destination doesn't allow, or\n"
                  "  \u2022 the destination is FAT32 and the file is 4 GB or larger.",
}


def name_problems(name):
    """List reasons `name` (one file or folder name) isn't valid on Windows. Empty if it is fine."""
    problems = []
    bad = sorted({c for c in name if c in WINDOWS_BAD_CHARS})
    if bad:
        problems.append("contains " + " ".join(repr(c) for c in bad) + ", which Windows doesn't allow in names")
    if any(ord(c) < 32 for c in name):
        problems.append("contains an invisible control character")
    if name != name.rstrip(" ."):
        problems.append("ends with a space or a dot, which Windows strips off")
    if name.split(".")[0].upper().rstrip() in WINDOWS_RESERVED:
        problems.append(f"uses the reserved Windows name {name.split('.')[0].upper()!r}")
    if len(name) > 255:
        problems.append(f"is {len(name)} characters long (the limit is 255)")
    return problems


def path_problems(path):
    """Name problems for every part of `path`, as ['"part" contains ...', ...]."""
    path = str(path)
    windows = os.name == "nt" or bool(re.match(r"^([A-Za-z]:|\\\\)", path))
    rest = re.sub(r"^(\\\\\?\\)?([A-Za-z]:)?", "", path)
    found = []
    for part in re.split(r"[\\/]+", rest):
        if part in ("", ".", ".."):
            continue
        problems = name_problems(part)
        if not windows:     # macOS/Linux accept these; only control characters and length matter
            problems = [p for p in problems if "control" in p or "characters long" in p]
        for problem in problems:
            found.append(f'"{part}" {problem}')
    if windows and len(path) >= 260 and not path.startswith("\\\\?\\"):
        found.append(f"the full path is {len(path)} characters long (Windows' usual limit is 259)")
    return found


def safe_name(name, replacement="_"):
    """`name` with anything Windows won't accept replaced, so the copy can still be made."""
    cleaned = "".join(replacement if (c in WINDOWS_BAD_CHARS or ord(c) < 32)
                      else c for c in name)
    stripped = cleaned.rstrip(" .")
    if stripped != cleaned:
        cleaned = stripped + replacement
    if cleaned.split(".")[0].upper() in WINDOWS_RESERVED:
        cleaned = replacement + cleaned
    if len(cleaned) > 255:
        stem, ext = os.path.splitext(cleaned)
        cleaned = stem[:255 - len(ext)] + ext
    return cleaned or replacement


def _code_hint(exc):
    winerror = getattr(exc, "winerror", None)
    if winerror in WINERROR_HINTS:
        return WINERROR_HINTS[winerror]
    return ERRNO_HINTS.get(getattr(exc, "errno", None))


def _raw(exc):
    code = getattr(exc, "winerror", None)
    text = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
    if code:
        return f"[WinError {code}] {text}"
    if getattr(exc, "errno", None):
        return f"[Errno {exc.errno}] {text}"
    return f"{type(exc).__name__}: {text}"


def explain(exc, path=None, action=None, extra=None, hint=None):
    """A multi-line message: what failed, on which file, why, and the original error."""
    paths = [p for p in (path, getattr(exc, "filename", None), getattr(exc, "filename2", None)) if p]
    paths = list(dict.fromkeys(str(p) for p in paths))
    lines = [f"Couldn't {action}." if action else "Something went wrong."]
    for p in paths:
        lines.append(f"File: {p}")
    if extra:
        lines.append(extra)
    lines.append("")
    lines.append(MARKER)
    name_issues = [issue for p in paths for issue in path_problems(p)]
    if name_issues:
        lines.append("The name has a problem:")
        lines.extend(f"  \u2022 {issue}" for issue in name_issues)
        lines.append("Rename it (or the folder it's in) and run again.")
    hint = hint or _code_hint(exc)
    if hint and not (name_issues and getattr(exc, "errno", None) == errno.EINVAL):
        lines.append(hint)
    if not hint and not name_issues:
        lines.append("No specific cause was recognised. The details below are what the system reported.")
    lines.append("")
    lines.append("Details: " + _raw(exc))
    return "\n".join(lines)


def short_reason(exc, path=None, hint=None):
    """One line for a log: the name problem or the first sentence of the hint, then the raw error."""
    issues = path_problems(path) if path else []
    if issues:
        return "bad name: " + issues[0] + f" ({_raw(exc)})"
    hint = hint or _code_hint(exc)
    if hint and getattr(exc, "errno", None) != errno.EINVAL:
        return hint.split("\n")[0].split(". ")[0].rstrip(".:") + f" ({_raw(exc)})"
    if hint and hint is not ERRNO_HINTS[errno.EINVAL]:
        return hint.split("\n")[0].split(". ")[0].rstrip(".:") + f" ({_raw(exc)})"
    return _raw(exc)


def is_drive_error(exc):
    """True for errors that usually mean the drive itself failed or disappeared mid-transfer."""
    return (getattr(exc, "winerror", None) in {21, 23, 59, 64, 87, 121, 1117, 1167, 1392}
            or getattr(exc, "errno", None) in {errno.EINVAL, errno.EIO, errno.ENXIO, errno.ENODEV})


READ_HINT = ("The drive couldn't read this part of the file. The card or drive probably disconnected "
             "for a moment or has a bad spot, or the card reader or cable lost contact.\n"
             "Try: reseat the card or use another reader/port/cable, then run again (copied files are "
             "skipped). If it fails at the same spot every time, that clip is damaged on the card; copy "
             "it with the camera maker's software or recovery tools.")
WRITE_HINT = ("The destination drive rejected the write. Usually it disconnected or went to sleep, is "
              "FAT32 and the file is 4 GB or larger, or is a network drive that dropped.\n"
              "Try: check the destination drive is connected and not FAT32, then run again.")


_CODE_RE = re.compile(r"\[(WinError|Errno) (\d+)\]")
_QUOTED_RE = re.compile(r"'((?:[A-Za-z]:|\\\\|/)[^']*)'|\"((?:[A-Za-z]:|\\\\|/)[^\"]*)\"")


def add_context(message):
    """Append a plain-English hint to a message that carries a raw OS error. Others are left alone."""
    message = str(message)
    if MARKER in message:
        return message
    match = _CODE_RE.search(message)
    paths = [a or b for a, b in _QUOTED_RE.findall(message)]
    issues = [issue for p in paths for issue in path_problems(p)]
    hint = None
    if match:
        kind, code = match.group(1), int(match.group(2))
        hint = WINERROR_HINTS.get(code) if kind == "WinError" else ERRNO_HINTS.get(code)
    if not hint and not issues:
        return message
    extra = ["", MARKER]
    if issues:
        extra.append("The name has a problem:")
        extra.extend(f"  \u2022 {issue}" for issue in issues)
    if hint and not (issues and match and match.group(1) == "Errno" and int(match.group(2)) == errno.EINVAL):
        extra.append(hint)
    if match and not paths:
        extra.append("(The system didn't say which file. If the tool has a log, the last file listed "
                     "there is the one it was working on.)")
    return message + "\n" + "\n".join(extra)


def install():
    """Make every messagebox.showerror in the app add a hint to raw OS errors."""
    original = messagebox.showerror
    if getattr(original, "_autoae_error_help", False):
        return

    def showerror(title=None, message=None, **options):
        try:
            message = add_context(message) if message is not None else message
        except Exception:
            pass
        return original(title, message, **options)

    showerror._autoae_error_help = True
    messagebox.showerror = showerror


if __name__ == "__main__":      # quick look: python error_help.py
    print(explain(OSError(errno.EINVAL, "Invalid argument"), r"E:\CONTENTS\CLIPS001\A002C068.MXF",
                  "copy this clip"))
    print()
    print(add_context(r"Failed to copy: [Errno 22] Invalid argument: 'D:\\Media\\Scene 12: take 3?.mov'"))
    sys.exit(0)
