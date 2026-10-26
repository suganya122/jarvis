"""Read the clipboard as text, per OS, with no extra dependencies. Only ever called when
the user turned clipboard sharing on."""

import ctypes
import os
import shutil
import subprocess
import sys

MAX_CHARS = 20_000


def _command() -> list[str] | None:
    if sys.platform == "darwin":
        return ["pbpaste"]
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        return ["wl-paste", "--no-newline", "--type", "text"]
    if shutil.which("xclip"):
        return ["xclip", "-o", "-selection", "clipboard"]
    if shutil.which("xsel"):
        return ["xsel", "--clipboard", "--output"]
    return None


if sys.platform == "win32":

    def _windows() -> str | None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(pointer)[:MAX_CHARS]
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

else:

    def _windows() -> str | None:
        return None


def read() -> str | None:
    """The clipboard's text, '' when it holds no text, None when it can't be read here."""
    if sys.platform == "win32":
        return _windows()
    argv = _command()
    if argv is None:
        return None
    try:
        out = subprocess.run(argv, capture_output=True, timeout=2, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.decode("utf-8", errors="replace")[:MAX_CHARS] if out.returncode == 0 else ""
