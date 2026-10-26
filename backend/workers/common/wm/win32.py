"""Windows: the Win32 window API through ctypes (EnumWindows, SetWindowPos, monitor work areas)."""

import ctypes
import sys
from ctypes import wintypes
from typing import Any

from . import Screen, Win, WindowManagerError

assert sys.platform == "win32"

SW_RESTORE, SW_MAXIMIZE = 9, 3
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x00000080
GW_OWNER, DWMWA_CLOAKED = 4, 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITORINFOF_PRIMARY = 1


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]  # fmt: skip


ENUM_WINDOWS = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
ENUM_MONITORS = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)


def _libs() -> tuple[Any, Any, Any]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    with_dpi = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if with_dpi is not None:
        with_dpi(ctypes.c_void_p(-4))  # per-monitor v2: real pixels, consistent across screens
    return user32, kernel32, dwmapi


def _exe(kernel32: Any, user32: Any, hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe").removesuffix(".EXE")
        return ""
    finally:
        kernel32.CloseHandle(handle)


class Win32Windows:
    name = "Windows"

    def __init__(self) -> None:
        self._user32, self._kernel32, self._dwm = _libs()

    def windows(self) -> list[Win]:
        user32 = self._user32
        found: list[Win] = []
        focused = user32.GetForegroundWindow()

        def visit(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            cloaked = wintypes.DWORD()
            self._dwm.DwmGetWindowAttribute(
                hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )
            length = user32.GetWindowTextLengthW(hwnd)
            if cloaked.value or length == 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            found.append(
                Win(
                    id=str(hwnd),
                    app=_exe(self._kernel32, user32, hwnd),
                    title=title.value,
                    x=rect.left,
                    y=rect.top,
                    width=rect.right - rect.left,
                    height=rect.bottom - rect.top,
                    minimized=bool(user32.IsIconic(hwnd)),
                    focused=hwnd == focused,
                )
            )
            return True

        user32.EnumWindows(ENUM_WINDOWS(visit), 0)
        return found

    def screens(self) -> list[Screen]:
        user32 = self._user32
        found: list[Screen] = []

        def visit(monitor: int, _hdc: int, _rect: Any, _lparam: int) -> bool:
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(monitor, ctypes.byref(info))
            work = info.rcWork
            found.append(
                Screen(
                    index=len(found),
                    x=work.left,
                    y=work.top,
                    width=work.right - work.left,
                    height=work.bottom - work.top,
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
            return True

        user32.EnumDisplayMonitors(None, None, ENUM_MONITORS(visit), 0)
        return found

    def place(self, win: Win, x: int, y: int, width: int, height: int) -> None:
        hwnd = int(win.id)
        self._user32.ShowWindow(hwnd, SW_RESTORE)
        if not self._user32.SetWindowPos(hwnd, None, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE):
            raise WindowManagerError(f"couldn't move the {win.app} window")

    def maximize(self, win: Win, screen: Screen) -> None:
        self.place(win, screen.x, screen.y, screen.width, screen.height)
        self._user32.ShowWindow(int(win.id), SW_MAXIMIZE)

    def focus(self, win: Win) -> None:
        self._user32.SetForegroundWindow(int(win.id))
