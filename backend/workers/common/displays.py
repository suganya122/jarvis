"""Displays and the pointer, per OS, with no extra dependencies (ctypes or a stock command).

Displays are named the way people talk about them: 'Screen 1', 'Screen 2', … ordered left
to right, then top to bottom. Coordinates are the desktop's (they can be negative).
"""

import contextlib
import ctypes
import ctypes.util
import re
import shutil
import subprocess
import sys

from contracts.context import Display

Raw = tuple[int, int, int, int, float, bool]  # x, y, width, height, scale, primary


def name_displays(raw: list[Raw]) -> list[Display]:
    ordered = sorted(raw, key=lambda r: (r[0], r[1]))
    return [
        Display(display_id=f"d{i}", name=f"Screen {i}", x=x, y=y, width=w, height=h, scale=s, primary=p)
        for i, (x, y, w, h, s, p) in enumerate(ordered, start=1)
    ]


def display_at(px: int, py: int, displays: list[Display] | tuple[Display, ...]) -> Display | None:
    """The display containing the point, else the nearest (pointer in a gap between displays)."""
    if not displays:
        return None
    for d in displays:
        if d.contains(px, py):
            return d
    return min(displays, key=lambda d: d.distance(px, py))


_XRANDR = re.compile(r"(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)")


def parse_xrandr(text: str) -> list[Raw]:
    """`xrandr --listmonitors`: ` 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1` ('*' = primary)."""
    found: list[Raw] = []
    for line in text.splitlines()[1:]:
        if m := _XRANDR.search(line):
            w, h, x, y = (int(g) for g in m.groups())
            found.append((x, y, w, h, 1.0, "*" in line.split()[1]))
    return found


def _x11() -> list[Raw]:
    exe = shutil.which("xrandr")
    if exe is None:
        return []
    try:
        argv = [exe, "--listmonitors"]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=2, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_xrandr(out.stdout) if out.returncode == 0 else []


if sys.platform == "win32":
    from ctypes import wintypes

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]  # fmt: skip

    def make_dpi_aware() -> None:
        """Real pixels everywhere (otherwise Windows scales coordinates on 125-200% displays)."""
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2

    def _windows() -> list[Raw]:
        found: list[Raw] = []
        user32 = ctypes.windll.user32
        try:
            shcore = ctypes.windll.shcore
        except OSError:
            shcore = None
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)  # fmt: skip

        def callback(hmon: int, _hdc: int, _rect: object, _data: int) -> int:
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcMonitor
                dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
                if shcore is not None:
                    shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                primary = bool(info.dwFlags & 1)
                found.append((r.left, r.top, r.right - r.left, r.bottom - r.top, dpi_x.value / 96, primary))
            return 1

        user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
        return found

    def _pointer_windows() -> tuple[int, int] | None:
        point = wintypes.POINT()
        return (point.x, point.y) if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)) else None

else:

    def make_dpi_aware() -> None:
        pass

    def _windows() -> list[Raw]:
        return []

    def _pointer_windows() -> tuple[int, int] | None:
        return None


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


def _coregraphics() -> ctypes.CDLL:
    return ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")


def _macos() -> list[Raw]:
    cg = _coregraphics()
    cg.CGDisplayBounds.restype = _CGRect
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
    cg.CGDisplayModeGetPixelWidth.argtypes = [ctypes.c_void_p]
    cg.CGDisplayModeGetPixelWidth.restype = ctypes.c_size_t
    cg.CGDisplayModeRelease.argtypes = [ctypes.c_void_p]
    main = cg.CGMainDisplayID()
    ids = (ctypes.c_uint32 * 16)()
    count = ctypes.c_uint32()
    if cg.CGGetActiveDisplayList(16, ids, ctypes.byref(count)) != 0:
        return []
    found: list[Raw] = []
    for display in ids[: count.value]:
        b = cg.CGDisplayBounds(display)  # points, global, top-left origin
        mode = cg.CGDisplayCopyDisplayMode(display)
        scale = 1.0
        if mode:
            scale = cg.CGDisplayModeGetPixelWidth(mode) / max(b.size.width, 1)
            cg.CGDisplayModeRelease(mode)
        x, y, w, h = int(b.origin.x), int(b.origin.y), int(b.size.width), int(b.size.height)
        found.append((x, y, w, h, round(scale, 2), display == main))
    return found


def _pointer_macos() -> tuple[int, int] | None:
    cg = _coregraphics()
    cg.CGEventCreate.restype = ctypes.c_void_p
    cg.CGEventCreate.argtypes = [ctypes.c_void_p]
    cg.CGEventGetLocation.restype = _CGPoint
    cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
    event = cg.CGEventCreate(None)
    if not event:
        return None
    point = cg.CGEventGetLocation(event)
    return int(point.x), int(point.y)


def _pointer_x11() -> tuple[int, int] | None:
    path = ctypes.util.find_library("X11")
    if not path:
        return None
    x = ctypes.cdll.LoadLibrary(path)
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x.XOpenDisplay(None)
    if not display:
        return None
    try:
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy, mask = (ctypes.c_int() for _ in range(5))
        ok = x.XQueryPointer(ctypes.c_void_p(display), x.XDefaultRootWindow(display), ctypes.byref(root),
                             ctypes.byref(child), ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(wx),
                             ctypes.byref(wy), ctypes.byref(ctypes.c_uint()))  # fmt: skip
        del mask
        return (rx.value, ry.value) if ok else None
    finally:
        x.XCloseDisplay(display)


def displays() -> list[Display]:
    try:
        if sys.platform == "win32":
            raw = _windows()
        elif sys.platform == "darwin":
            raw = _macos()
        else:
            raw = _x11()
    except (OSError, AttributeError, ValueError):
        raw = []
    return name_displays(raw)


def pointer() -> tuple[int, int] | None:
    """Where the mouse pointer is, or None (e.g. Wayland, where apps can't ask)."""
    try:
        if sys.platform == "win32":
            return _pointer_windows()
        if sys.platform == "darwin":
            return _pointer_macos()
        return _pointer_x11()
    except (OSError, AttributeError, ValueError):
        return None
