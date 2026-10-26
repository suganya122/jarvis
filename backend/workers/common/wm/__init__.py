"""Window placement across operating systems: list windows and screens, move and resize.

Backends:
- GNOME (Wayland or X11): the bundled Shell extension (gnome_extension/), over D-Bus. On
  Wayland it's the only way: apps can't move other apps' windows.
- Other X11 desktops: wmctrl.
- macOS: System Events (needs Accessibility permission), driven through osascript.
- Windows: the Win32 window API through ctypes.
"""

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Win:
    id: str
    app: str
    title: str
    x: int
    y: int
    width: int
    height: int
    screen: int = -1
    minimized: bool = False
    focused: bool = False


@dataclass(frozen=True)
class Screen:
    """A display's work area: the part windows may use (no top bar, dock or taskbar)."""

    index: int
    x: int
    y: int
    width: int
    height: int
    primary: bool = False
    builtin: bool = False
    name: str = ""

    @property
    def area(self) -> int:
        return self.width * self.height


class WindowManagerError(Exception):
    pass


class WindowManager(Protocol):
    name: str

    def windows(self) -> list[Win]: ...

    def screens(self) -> list[Screen]: ...

    def place(self, win: Win, x: int, y: int, width: int, height: int) -> None: ...

    def maximize(self, win: Win, screen: Screen) -> None: ...

    def focus(self, win: Win) -> None: ...


GNOME_HELP = (
    "On GNOME, window placement needs the assistant's GNOME Shell extension: run "
    "`python -m core.cli setup-windows`, then log out and back in once."
)


def detect() -> WindowManager:
    """The backend for this desktop, or WindowManagerError saying what to do."""
    if sys.platform == "darwin":
        from .mac import MacWindows

        return MacWindows()
    if sys.platform == "win32":
        from .win32 import Win32Windows

        return Win32Windows()
    from .gnome import GnomeWindows

    gnome = GnomeWindows()
    if gnome.available():
        return gnome
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    if "gnome" in desktop:
        raise WindowManagerError(GNOME_HELP)
    if not wayland and os.environ.get("DISPLAY") and shutil.which("wmctrl"):
        from .x11 import X11Windows

        return X11Windows()
    if wayland:
        raise WindowManagerError("this Wayland desktop doesn't let apps move other windows")
    raise WindowManagerError("window placement needs wmctrl (Ubuntu/Debian: sudo apt install wmctrl)")
