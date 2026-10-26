"""Starts the on-screen highlight helper, one at a time. Optional: the UI always shows it too."""

import json
import os
import subprocess
import sys
from functools import cache
from pathlib import Path

HELPER = Path(__file__).with_name("overlay_gtk.py")
SYSTEM_PYTHON = "/usr/bin/python3"


@cache
def _gtk_available() -> bool:
    if not Path(SYSTEM_PYTHON).exists():
        return False
    probe = subprocess.run(  # noqa: S603 - fixed interpreter and fixed probe
        [SYSTEM_PYTHON, "-c", "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"],
        capture_output=True,
        timeout=20,
    )
    return probe.returncode == 0


def unavailable_reason() -> str | None:
    if not sys.platform.startswith("linux"):
        return "on-screen highlights are only implemented on Linux so far"
    if not os.environ.get("DISPLAY"):
        return "no X display (XWayland) for the on-screen highlight"
    if not _gtk_available():
        return "the on-screen highlight needs python3-gi (GTK 3)"
    return None


class Overlay:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    def show(
        self, x: int, y: int, w: int, h: int, desktop_w: int, desktop_h: int, label: str, seconds: float
    ) -> bool:
        if unavailable_reason() is not None:
            return False
        self.hide()
        spec = {"x": x, "y": y, "w": w, "h": h, "desktop_w": desktop_w, "desktop_h": desktop_h,
                "label": label, "seconds": seconds}  # fmt: skip
        env = {**os.environ, "GDK_BACKEND": "x11"}  # XWayland: absolute positions are allowed
        self._proc = subprocess.Popen(  # noqa: S603 - our helper, JSON argument, no shell
            [SYSTEM_PYTHON, str(HELPER), json.dumps(spec)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        assert self._proc.stdout is not None
        return self._proc.stdout.readline().startswith(b"SHOWN")

    def hide(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None
