"""On-screen highlight: four thin bars around a box plus a label. Run with the system python3.

Usage: python3 overlay_gtk.py '<json>' with keys x, y, w, h, desktop_w, desktop_h, label, seconds.
Coordinates are in full-desktop capture pixels; they're mapped to the X root (XWayland on
Wayland). The box's middle stays empty, so clicks go straight through to the target.
"""

import json
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

BAR = 4
CSS = b"""
.hl-bar { background-color: #e5484d; }
.hl-label { background-color: #ffd60a; color: #111; padding: 4px 8px; font-weight: bold; }
"""


def popup(x: int, y: int, w: int, h: int, css_class: str, child: Gtk.Widget | None = None) -> Gtk.Window:
    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.get_style_context().add_class(css_class)
    win.set_keep_above(True)
    win.set_accept_focus(False)
    if child is not None:
        win.add(child)
    win.move(x, y)
    win.set_default_size(max(1, w), max(1, h))
    win.resize(max(1, w), max(1, h))
    return win


def main() -> int:
    spec = json.loads(sys.argv[1])
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    screen = Gdk.Screen.get_default()
    Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    sx = screen.get_width() / max(1, spec["desktop_w"])
    sy = screen.get_height() / max(1, spec["desktop_h"])
    x, y = round(spec["x"] * sx), round(spec["y"] * sy)
    w, h = max(8, round(spec["w"] * sx)), max(8, round(spec["h"] * sy))
    pad = BAR + 2
    windows = [
        popup(x - pad, y - pad, w + 2 * pad, BAR, "hl-bar"),
        popup(x - pad, y + h + 2, w + 2 * pad, BAR, "hl-bar"),
        popup(x - pad, y - pad, BAR, h + 2 * pad, "hl-bar"),
        popup(x + w + 2, y - pad, BAR, h + 2 * pad, "hl-bar"),
    ]
    if spec.get("label"):
        label = Gtk.Label(label=str(spec["label"])[:60])
        label_y = y - pad - 30 if y - pad - 30 > 0 else y + h + pad + 2
        windows.append(popup(x - pad, label_y, 10, 26, "hl-label", label))
    for win in windows:
        win.show_all()
    print(f"SHOWN {x} {y} {w} {h}", flush=True)
    GLib.timeout_add(int(float(spec.get("seconds", 4)) * 1000), Gtk.main_quit)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
