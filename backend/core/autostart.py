"""Opt-in start at login, per OS. The OS entry itself is the setting (nothing else to sync):
Linux: an XDG autostart .desktop file; macOS: a LaunchAgent plist; Windows: a value under
HKCU\\...\\Run. All are per-user and need no admin rights."""

import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from contracts import APP_ID, APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command(python: str | None = None) -> list[str]:
    """How the daemon is started at login (no browser tab, no console window on Windows)."""
    exe = python or sys.executable
    if sys.platform == "win32" and exe.lower().endswith("python.exe"):
        exe = exe[: -len("python.exe")] + "pythonw.exe"
    return [exe, "-m", "core"]


class Autostart:
    def __init__(
        self, platform: str = sys.platform, home: Path | None = None, command: list[str] | None = None
    ) -> None:
        self._platform = platform
        self._home = home or Path.home()
        self._command = command or launch_command()

    @property
    def supported(self) -> bool:
        return self._platform in ("linux", "darwin", "win32")

    def _file(self) -> Path:
        if self._platform == "darwin":
            return self._home / "Library" / "LaunchAgents" / f"{APP_ID}.plist"
        return self._home / ".config" / "autostart" / f"{APP_ID}.desktop"

    def enabled(self) -> bool:
        if self._platform == "win32":
            return _win_get() is not None
        return self._file().exists()

    def enable(self) -> None:
        if self._platform == "win32":
            _win_set(subprocess.list2cmdline(self._command))
            return
        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._platform == "darwin":
            plist = {"Label": APP_ID, "ProgramArguments": self._command, "RunAtLoad": True}
            path.write_bytes(plistlib.dumps(plist))
        else:
            path.write_text(
                "[Desktop Entry]\nType=Application\n"
                f"Name={APP_NAME}\nComment=Start {APP_NAME} at login\n"
                f"Exec={shlex.join(self._command)}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )

    def disable(self) -> None:
        if self._platform == "win32":
            _win_delete()
            return
        self._file().unlink(missing_ok=True)


if sys.platform == "win32":
    import winreg

    def _win_get() -> str | None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, APP_ID)
                return str(value)
        except OSError:
            return None

    def _win_set(command: str) -> None:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, command)

    def _win_delete() -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_ID)
        except OSError:
            pass

else:

    def _win_get() -> str | None:
        raise OSError("the Windows registry only exists on Windows")

    def _win_set(command: str) -> None:
        raise OSError("the Windows registry only exists on Windows")

    def _win_delete() -> None:
        raise OSError("the Windows registry only exists on Windows")
