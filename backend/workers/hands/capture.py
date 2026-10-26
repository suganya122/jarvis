"""Screen capture backends. On demand only; images live in memory and are never kept.

- Wayland: the xdg-desktop-portal Screenshot API (the only sanctioned way; GNOME may ask
  the user once). 'select' opens the desktop's own region/window picker.
- X11, macOS, Windows: Pillow's ImageGrab.
"""

import io
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from contracts.capture import CaptureError, CaptureKind
from contracts.context import Display
from workers.common.displays import displays

PORTAL_TIMEOUT_S = 120.0
ALLOW_SILENT_COMMAND = (
    "gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore "
    "--object-path /org/freedesktop/impl/portal/PermissionStore "
    "--method org.freedesktop.impl.portal.PermissionStore.SetPermission "
    "screenshot true screenshot '' \"['yes']\""
)


@dataclass
class RawCapture:
    image: Image.Image
    source: str
    offset_x: int = 0
    """Where this image sits inside the full desktop capture (for window crops)."""
    offset_y: int = 0
    desktop_width: int = 0
    desktop_height: int = 0


def session_kind() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    return "x11" if os.environ.get("DISPLAY") else "none"


def _load_and_delete(uri: str, started: float) -> Image.Image:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise CaptureError("the screenshot portal returned an unexpected location")
    path = Path(unquote(parsed.path))
    try:
        data = path.read_bytes()
    finally:
        # The portal writes a file for us; we never keep screenshots on disk.
        try:
            if path.stat().st_mtime >= started - 5:
                path.unlink()
        except OSError:
            pass
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def portal_screenshot(interactive: bool) -> Image.Image:
    from jeepney import DBusAddress, new_method_call
    from jeepney.bus_messages import MatchRule, message_bus
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import DBusErrorResponse, unwrap_msg

    started = time.time()
    conn = open_dbus_connection(bus="SESSION")
    try:
        token = "assistant_" + secrets.token_hex(6)
        sender = conn.unique_name.lstrip(":").replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        rule = MatchRule(
            type="signal", interface="org.freedesktop.portal.Request", member="Response", path=handle
        )
        unwrap_msg(conn.send_and_get_reply(message_bus.AddMatch(rule)))
        portal = DBusAddress(
            "/org/freedesktop/portal/desktop",
            bus_name="org.freedesktop.portal.Desktop",
            interface="org.freedesktop.portal.Screenshot",
        )
        options = {"handle_token": ("s", token), "interactive": ("b", interactive)}
        with conn.filter(rule) as queue:
            try:
                unwrap_msg(
                    conn.send_and_get_reply(new_method_call(portal, "Screenshot", "sa{sv}", ("", options)))
                )
            except DBusErrorResponse as exc:
                raise CaptureError(f"the screenshot portal is unavailable: {exc}") from exc
            try:
                signal = conn.recv_until_filtered(queue, timeout=PORTAL_TIMEOUT_S)
            except TimeoutError as exc:
                what = "nothing was picked in time" if interactive else "no answer from the screenshot prompt"
                raise CaptureError(what) from exc
        code, results = signal.body
    finally:
        conn.close()
    if code == 1:
        raise CaptureError("the screenshot was cancelled")
    if code != 0 or "uri" not in results:
        if interactive:
            raise CaptureError("the desktop didn't allow the screenshot")
        raise CaptureError(
            "GNOME doesn't allow silent screenshots for this app. Use 'Pick a region' instead (you choose "
            f"what to share), or allow silent screenshots once with: {ALLOW_SILENT_COMMAND}"
        )
    return _load_and_delete(str(results["uri"][1]), started)


def crop_box(
    bounds: tuple[int, int, int, int], displays: list[Display], image_size: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Desktop coordinates -> pixels of a whole-desktop image. The image starts at the
    top-left of all displays (which can be negative) and may be in physical pixels (HiDPI)."""
    if displays:
        ox, oy = min(d.x for d in displays), min(d.y for d in displays)
        vw = max(d.x + d.width for d in displays) - ox
        vh = max(d.y + d.height for d in displays) - oy
    else:
        ox, oy, vw, vh = 0, 0, image_size[0], image_size[1]
    sx, sy = image_size[0] / max(vw, 1), image_size[1] / max(vh, 1)
    x, y, w, h = bounds
    left, top = round((x - ox) * sx), round((y - oy) * sy)
    right, bottom = round((x - ox + w) * sx), round((y - oy + h) * sy)
    return max(0, left), max(0, top), min(image_size[0], right), min(image_size[1], bottom)


def grab(kind: CaptureKind, window_bounds: tuple[int, int, int, int] | None) -> RawCapture:
    session = session_kind()
    if session == "wayland":
        if kind == "window":
            raise CaptureError(
                "on Wayland, capture the whole screen or let the user pick the window (select)"
            )
        image = portal_screenshot(interactive=kind == "select")
        return RawCapture(image, "portal", desktop_width=image.width, desktop_height=image.height)
    if kind == "select":
        raise CaptureError(
            "picking a region is only available on Wayland desktops so far; capture a window instead"
        )
    from PIL import ImageGrab

    if session == "x11":
        full = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY"))
    elif session in ("macos", "windows"):
        full = ImageGrab.grab(all_screens=True)
    else:
        raise CaptureError("no display to capture")
    raw = RawCapture(full, session, desktop_width=full.width, desktop_height=full.height)
    if kind == "window":
        if window_bounds is None:
            raise CaptureError("that window's position isn't known")
        box = crop_box(window_bounds, displays(), full.size)
        if box[2] - box[0] < 10 or box[3] - box[1] < 10:
            raise CaptureError("that window isn't on screen")
        raw.image = full.crop(box)
        raw.offset_x, raw.offset_y = box[0], box[1]
    return raw
