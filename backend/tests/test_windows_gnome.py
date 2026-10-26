"""The GNOME Shell extension, for real: a private, headless GNOME Shell (its own D-Bus session,
settings and folders, nothing shared with the desktop), two test windows, and the
assistant's backend arranging them over D-Bus."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
EXTENSION = BACKEND / "workers" / "common" / "wm" / "gnome_extension"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or not all(shutil.which(t) for t in ("gnome-shell", "dbus-run-session", "gsettings")),
    reason="needs GNOME Shell",
)

WINDOW = """
import sys, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
w = Gtk.Window(title=sys.argv[1]); w.set_default_size(400, 300); w.show_all()
GLib.timeout_add_seconds(40, Gtk.main_quit); Gtk.main()
"""

CHECK = """
from workers.common.wm.gnome import GnomeWindows
from workers.os_tools.window_tools import Placement, arrange
wm = GnomeWindows()
screen = wm.screens()[0]
assert screen.y > 0, screen  # the work area leaves out the top bar
pair = [Placement(app="window-one", slot="left"), Placement(app="window-two", slot="right")]
lines, ok = arrange(wm, pair, lambda _a: False)
assert ok, lines
after = {w.title: w for w in wm.windows()}
one, two = after["window-one"], after["window-two"]
assert (one.x, one.width) == (screen.x, screen.width // 2), one
assert (two.x, two.width) == (screen.x + screen.width // 2, screen.width // 2), two
lines, ok = arrange(wm, [Placement(app="window-one", slot="full")], lambda _a: False)
assert ok, lines
print("ARRANGED")
"""

SCRIPT = """
set -u
gsettings set org.gnome.shell disable-user-extensions false
gsettings set org.gnome.shell enabled-extensions "['windows@local-assistant']"
gnome-shell --headless --no-x11 --virtual-monitor 1280x800 --wayland-display wl-t > "$ROOT/shell.log" 2>&1 &
SHELL_PID=$!
for i in $(seq 80); do
  gdbus call --session --dest org.localassistant.Windows --object-path /org/localassistant/Windows \
    --method org.localassistant.Windows.Screens >/dev/null 2>&1 && break
  sleep 0.25
done
WAYLAND_DISPLAY=wl-t GDK_BACKEND=wayland /usr/bin/python3 "$ROOT/window.py" window-one >/dev/null 2>&1 &
WAYLAND_DISPLAY=wl-t GDK_BACKEND=wayland /usr/bin/python3 "$ROOT/window.py" window-two >/dev/null 2>&1 &
sleep 3
cd "$BACKEND" && "$PYTHON" -c "$CHECK"
kill $SHELL_PID 2>/dev/null
wait $SHELL_PID 2>/dev/null
"""


def test_the_extension_arranges_real_windows_in_a_private_gnome_shell() -> None:
    root = Path(
        subprocess.check_output(["mktemp", "-d", "/tmp/jwm.XXXX"], text=True).strip()
    )  # sockets: < 108 chars
    try:
        for sub in ("config", "data/gnome-shell/extensions", "cache", "state", "runtime"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        (root / "runtime").chmod(0o700)
        shutil.copytree(EXTENSION, root / "data/gnome-shell/extensions/windows@local-assistant")
        (root / "window.py").write_text(WINDOW)
        env = {
            "HOME": os.environ["HOME"], "PATH": os.environ["PATH"], "USER": os.environ.get("USER", ""),
            "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data"),
            "XDG_CACHE_HOME": str(root / "cache"), "XDG_STATE_HOME": str(root / "state"),
            "XDG_RUNTIME_DIR": str(root / "runtime"), "ROOT": str(root), "BACKEND": str(BACKEND),
            "PYTHON": sys.executable, "CHECK": CHECK,
        }  # fmt: skip
        started = time.monotonic()
        done = subprocess.run(["dbus-run-session", "--", "bash", "-c", SCRIPT], env=env, capture_output=True,
                              text=True, timeout=150, check=False)  # fmt: skip
        assert "ARRANGED" in done.stdout, (done.stdout[-2000:], done.stderr[-3000:])
        assert time.monotonic() - started < 150
    finally:
        shutil.rmtree(root, ignore_errors=True)
