"""X11 desktops other than GNOME: wmctrl for windows, xrandr for screens, _NET_WORKAREA for
the usable area."""

import re
import shutil
import subprocess

from ..displays import parse_xrandr
from . import Screen, Win, WindowManagerError

_WORKAREA = re.compile(r"=\s*(-?\d+),\s*(-?\d+),\s*(\d+),\s*(\d+)")


def _run(*argv: str) -> str:
    exe = shutil.which(argv[0])
    if exe is None:
        raise WindowManagerError(f"{argv[0]} isn't installed")
    done = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=5, check=False)  # noqa: S603
    if done.returncode != 0:
        raise WindowManagerError(f"{argv[0]} failed: {done.stderr.strip()[:160]}")
    return done.stdout


def parse_wmctrl(text: str) -> list[Win]:
    """`wmctrl -lGx`: `0x03a00003  0 120 80 960 1040  google-chrome.Google-chrome  host Title`."""
    found = []
    for line in text.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 8 or parts[1] == "-1":  # -1: sticky panels and docks
            continue
        title = parts[8] if len(parts) > 8 else ""
        x, y, w, h = (int(v) for v in parts[2:6])
        found.append(Win(id=parts[0], app=parts[6].split(".")[-1], title=title, x=x, y=y, width=w, height=h))
    return found


def clip(
    screen: tuple[int, int, int, int], work: tuple[int, int, int, int] | None
) -> tuple[int, int, int, int]:
    """A monitor cut down to the desktop's work area (no panels)."""
    if work is None:
        return screen
    x, y = max(screen[0], work[0]), max(screen[1], work[1])
    right = min(screen[0] + screen[2], work[0] + work[2])
    bottom = min(screen[1] + screen[3], work[1] + work[3])
    return (x, y, right - x, bottom - y) if right > x and bottom > y else screen


class X11Windows:
    name = "X11"

    def windows(self) -> list[Win]:
        return parse_wmctrl(_run("wmctrl", "-lGx"))

    def screens(self) -> list[Screen]:
        try:
            match = _WORKAREA.search(_run("xprop", "-root", "_NET_WORKAREA"))
            work = tuple(int(g) for g in match.groups()) if match else None
        except WindowManagerError:
            work = None
        raw = parse_xrandr(_run("xrandr", "--listmonitors"))
        screens = []
        for i, (x, y, w, h, _scale, primary) in enumerate(raw):
            cx, cy, cw, ch = clip((x, y, w, h), work)  # type: ignore[arg-type]
            screens.append(Screen(index=i, x=cx, y=cy, width=cw, height=ch, primary=primary))
        return screens

    def place(self, win: Win, x: int, y: int, width: int, height: int) -> None:
        _run("wmctrl", "-ir", win.id, "-b", "remove,maximized_vert,maximized_horz")
        _run("wmctrl", "-ir", win.id, "-e", f"0,{x},{y},{width},{height}")

    def maximize(self, win: Win, screen: Screen) -> None:
        self.place(win, screen.x, screen.y, screen.width, screen.height)
        _run("wmctrl", "-ir", win.id, "-b", "add,maximized_vert,maximized_horz")

    def focus(self, win: Win) -> None:
        _run("wmctrl", "-ia", win.id)
