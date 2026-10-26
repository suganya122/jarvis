"""A throwaway desktop for hands tests: private D-Bus + private a11y bus + Xvfb + fixture app.

Nothing here touches the user's real session: the runtime dir (where the a11y bus
socket lives) and the session bus are private, and the display is a virtual Xvfb.
"""

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "gtk_fixture_app.py"
SYSTEM_PYTHON = "/usr/bin/python3"
X_DIR = "/tmp/.X11-unix"


def missing_requirements() -> str | None:
    for tool in ("dbus-daemon", "Xvfb"):
        if shutil.which(tool) is None:
            return f"{tool} not installed"
    probe = subprocess.run(
        [SYSTEM_PYTHON, "-c", "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"],
        capture_output=True,
        timeout=30,
    )
    return None if probe.returncode == 0 else "python3-gi with GTK 3 not installed"


def _free_display() -> int:
    for n in range(90, 200):
        if not Path(f"{X_DIR}/X{n}").exists() and not Path(f"/tmp/.X{n}-lock").exists():
            return n
    raise RuntimeError("no free X display number")


@contextmanager
def a11y_session() -> Iterator[dict[str, str]]:
    runtime = tempfile.mkdtemp(prefix="hands-test-")
    os.chmod(runtime, 0o700)
    procs: list[subprocess.Popen[bytes]] = []
    try:
        bus = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "XDG_RUNTIME_DIR": runtime},
        )
        procs.append(bus)
        assert bus.stdout is not None
        address = bus.stdout.readline().decode().strip()
        display = _free_display()
        xvfb = subprocess.Popen(
            ["Xvfb", f":{display}", "-nolisten", "tcp", "-screen", "0", "1280x800x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(xvfb)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", runtime),
            "DBUS_SESSION_BUS_ADDRESS": address,
            "XDG_RUNTIME_DIR": runtime,
            "DISPLAY": f":{display}",
            "GDK_BACKEND": "x11",
            "XDG_SESSION_TYPE": "x11",
            # Blank, so nothing (e.g. the screenshot portal) can reach the user's real session.
            "WAYLAND_DISPLAY": "",
        }
        deadline = time.monotonic() + 10
        while not Path(f"{X_DIR}/X{display}").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Xvfb did not start")
            time.sleep(0.05)
        app = subprocess.Popen(
            [SYSTEM_PYTHON, str(FIXTURE)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env
        )
        procs.append(app)
        assert app.stdout is not None
        if app.stdout.readline().decode().strip() != "READY":
            raise RuntimeError("fixture app did not start")
        time.sleep(0.3)  # let the app register on the accessibility bus
        yield env
    finally:
        for proc in reversed(procs):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(runtime, ignore_errors=True)
