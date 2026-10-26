"""GNOME: talks to the bundled Shell extension (gnome_extension/) over the session bus."""

import json
from typing import Any

from . import GNOME_HELP, Screen, Win, WindowManagerError

BUS_NAME = "org.localassistant.Windows"
OBJECT_PATH = "/org/localassistant/Windows"
TIMEOUT_S = 3.0


class GnomeWindows:
    name = "GNOME"

    def _call(self, method: str, signature: str | None = None, body: tuple[Any, ...] = ()) -> Any:
        try:
            from jeepney import DBusAddress, new_method_call
            from jeepney.io.blocking import open_dbus_connection
        except ImportError as exc:
            raise WindowManagerError(f"D-Bus isn't available: {exc}") from exc
        address = DBusAddress(OBJECT_PATH, bus_name=BUS_NAME, interface=BUS_NAME)
        try:
            with open_dbus_connection(bus="SESSION") as conn:
                message = (
                    new_method_call(address, method, signature, body)
                    if signature
                    else new_method_call(address, method)
                )
                reply = conn.send_and_get_reply(message, timeout=TIMEOUT_S)
        except Exception as exc:
            raise WindowManagerError(f"{GNOME_HELP} ({type(exc).__name__})") from exc
        if reply.header.message_type.name == "error":
            raise WindowManagerError(GNOME_HELP)
        return reply.body[0] if reply.body else None

    def available(self) -> bool:
        try:
            self._call("Screens")
            return True
        except WindowManagerError:
            return False

    def windows(self) -> list[Win]:
        return [
            Win(
                id=str(w["id"]),
                app=w["app"],
                title=w["title"],
                x=w["x"],
                y=w["y"],
                width=w["width"],
                height=w["height"],
                screen=w["monitor"],
                minimized=w["minimized"],
                focused=w["focused"],
            )
            for w in json.loads(self._call("List"))
        ]

    def screens(self) -> list[Screen]:
        return [
            Screen(
                index=s["index"],
                x=s["x"],
                y=s["y"],
                width=s["width"],
                height=s["height"],
                primary=s["primary"],
            )
            for s in json.loads(self._call("Screens"))
        ]

    def place(self, win: Win, x: int, y: int, width: int, height: int) -> None:
        if not self._call("Place", "tiiii", (int(win.id), x, y, width, height)):
            raise WindowManagerError(f"the {win.app} window has closed")

    def maximize(self, win: Win, screen: Screen) -> None:
        if not self._call("Maximize", "ti", (int(win.id), screen.index)):
            raise WindowManagerError(f"the {win.app} window has closed")

    def focus(self, win: Win) -> None:
        self._call("Focus", "t", (int(win.id),))
