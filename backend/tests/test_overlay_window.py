"""The real voice overlay window (Tk) on a private Xvfb display: placement on the
pointer's monitor, no focus stealing, keyboard after a click, hide, and the no-display path."""

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.a11y_session import _free_display

BACKEND = Path(__file__).resolve().parents[1]

# ---- the real window (Tk) on a private X display ---------------------------------------------


def _tk_available() -> bool:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


needs_x = pytest.mark.skipif(
    sys.platform != "linux"
    or not _tk_available()
    or not all(shutil.which(t) for t in ("Xvfb", "xdotool", "xwininfo")),
    reason="needs Linux with Tk, Xvfb, xdotool, xwininfo",
)
TWO_MONITORS = "[Rect(0, 0, 1920, 1080), Rect(1920, 0, 1920, 1080)]"


@pytest.fixture
def xdisplay() -> Iterator[dict[str, str]]:
    n = _free_display()
    xvfb = subprocess.Popen(
        ["Xvfb", f":{n}", "-nolisten", "tcp", "-screen", "0", "3840x1080x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = {**os.environ, "DISPLAY": f":{n}", "WAYLAND_DISPLAY": "", "PYTHONPATH": str(BACKEND)}
    for _ in range(100):
        if subprocess.run(["xdotool", "getmouselocation"], env=env, capture_output=True).returncode == 0:
            break
        time.sleep(0.05)
    yield env
    xvfb.terminate()
    xvfb.wait(5)


def _x(env: dict[str, str], *args: str) -> str:
    return subprocess.run(list(args), env=env, capture_output=True, text=True, timeout=10).stdout.strip()


def _window(env: dict[str, str]) -> dict[str, str]:
    for _ in range(100):
        if ids := _x(env, "xdotool", "search", "--name", "voice$").split():
            info = _x(env, "xwininfo", "-id", ids[-1])
            fields = dict(line.strip().split(": ", 1) for line in info.splitlines() if ": " in line)
            if fields.get("Map State") == "IsViewable":
                return fields | {"id": ids[-1]}
        time.sleep(0.05)
    raise AssertionError("overlay window never appeared")


@needs_x
def test_real_window_on_the_pointers_monitor_without_taking_focus(xdisplay: dict[str, str]) -> None:
    env = xdisplay
    other = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import tkinter as tk; r = tk.Tk(); r.title('other-app');"
            "r.geometry('300x200+100+100'); r.after(200, r.focus_force); r.mainloop()",
        ],
        env=env,
    )
    try:
        time.sleep(1.0)
        _x(env, "xdotool", "mousemove", "2600", "500")  # the pointer is on the right-hand display
        focused = _x(env, "xdotool", "getwindowfocus")
        boot = (
            "import workers.overlay.placement as p; from workers.overlay.placement import Rect;"
            f"p.monitors = lambda: {TWO_MONITORS}; from workers.overlay.__main__ import main; main()"
        )
        overlay = subprocess.Popen(
            [sys.executable, "-c", boot],
            env=env,
            cwd=BACKEND,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        assert overlay.stdin and overlay.stdout
        ready = json.loads(overlay.stdout.readline())
        assert ready["available"] and ready["detail"].endswith("X11")
        view = {
            "phase": "acting",
            "partial": "clear my downloads",
            "status": "Asking first.",
            "approval": {
                "approval_id": "a1",
                "tool": "delete",
                "risk": "destructive",
                "summary": "Delete 3 files",
            },
        }
        overlay.stdin.write(json.dumps({"type": "overlay.show", "view": view}) + "\n")
        overlay.stdin.flush()
        win = _window(env)
        x, w = int(win["Absolute upper-left X"]), int(win["Width"])
        assert x >= 1920 and x + w <= 3840, win  # on the pointer's display, fully
        assert int(win["Absolute upper-left X"]) == 1920 + 24  # top-left of the pointer's display
        assert int(win["Absolute upper-left Y"]) == 48 and win["Override Redirect State"] == "yes"
        assert _x(env, "xdotool", "getwindowfocus") == focused  # it never took the keyboard
        # Keyboard: click to focus it, then Y approves; Escape stops the request.
        _x(
            env,
            "xdotool",
            "mousemove",
            str(x + 20),
            str(int(win["Absolute upper-left Y"]) + 10),
            "click",
            "1",
        )
        time.sleep(0.3)
        _x(env, "xdotool", "key", "y")
        assert json.loads(overlay.stdout.readline()) == {
            "type": "overlay.decide",
            "approval_id": "a1",
            "approved": True,
        }
        _x(env, "xdotool", "key", "Escape")
        assert json.loads(overlay.stdout.readline()) == {"type": "overlay.stop"}
        overlay.stdin.write(json.dumps({"type": "overlay.hide"}) + "\n")
        overlay.stdin.flush()
        time.sleep(0.3)
        assert "IsUnMapped" in _x(env, "xwininfo", "-id", win["id"])
        overlay.stdin.close()
        assert overlay.wait(5) == 0
    finally:
        other.kill()


@needs_x
def test_without_a_display_it_reports_and_idles() -> None:
    env = {**os.environ, "DISPLAY": "", "PYTHONPATH": str(BACKEND)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "workers.overlay"],
        env=env,
        cwd=BACKEND,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout and proc.stdin
    ready = json.loads(proc.stdout.readline())
    assert ready["available"] is False and "display" in ready["detail"]
    proc.stdin.close()
    assert proc.wait(5) == 0
