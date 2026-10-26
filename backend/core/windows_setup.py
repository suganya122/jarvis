"""`python -m core.cli setup-windows`: what this computer needs before the assistant can arrange
windows ("Chrome on the left, Terminal on the right").

- GNOME (Ubuntu's desktop): installs the assistant's small GNOME Shell extension and switches it
  on. GNOME only loads new extensions at login, so it says to log out and back in once.
- macOS: opens System Settings at Accessibility, where the assistant needs to be allowed.
- Windows and other X11 desktops: nothing to set up (X11 needs `wmctrl`).
"""

import ast
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

UUID = "windows@local-assistant"
SOURCE = Path(__file__).resolve().parents[1] / "workers" / "common" / "wm" / "gnome_extension"
MAC_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def _gnome() -> bool:
    return (
        "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() and shutil.which("gsettings") is not None
    )


def extensions_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "gnome-shell" / "extensions" / UUID


def _gsettings(*args: str) -> str:
    done = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=10, check=True)  # noqa: S603, S607
    return done.stdout.strip()


def enabled_with(current: str, uuid: str = UUID) -> list[str] | None:
    """The enabled-extensions list with ours added, or None if it's already there."""
    text = current.removeprefix("@as ").strip()
    found = ast.literal_eval(text) if text.startswith("[") else []
    names = [str(x) for x in found]
    return None if uuid in names else [*names, uuid]


def setup(say: Callable[[str], None] = print) -> int:
    if sys.platform == "darwin":
        say("macOS: allow the assistant in System Settings → Privacy & Security → Accessibility.")
        subprocess.run(["open", MAC_PANE], check=False)  # noqa: S603, S607
        return 0
    if sys.platform == "win32":
        say("Windows: nothing to set up. Window placement works already.")
        return 0
    if not _gnome():
        if shutil.which("wmctrl"):
            say("This desktop uses wmctrl: window placement works already.")
            return 0
        say("Install wmctrl for window placement (Ubuntu/Debian: sudo apt install wmctrl).")
        return 1
    target = extensions_dir()
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SOURCE, target)
    if _gsettings("get", "org.gnome.shell", "disable-user-extensions") == "true":
        say("Note: GNOME has user extensions switched off (Extensions app → Use Extensions). Turn that on.")
    new = enabled_with(_gsettings("get", "org.gnome.shell", "enabled-extensions"))
    if new is not None:
        _gsettings("set", "org.gnome.shell", "enabled-extensions", str(new))
    say(f"Installed the window-placement extension ({target}).")
    say("GNOME loads new extensions at login: log out and back in once, then ask for a window layout.")
    return 0
