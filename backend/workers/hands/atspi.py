"""Minimal AT-SPI client over D-Bus (jeepney). Works on Wayland and X11.

AT-SPI is the Linux accessibility bus: every GTK/Qt/Chromium/Firefox app exposes its
widget tree there. We speak the D-Bus interfaces directly, so no compiled bindings.
"""

import threading
from typing import Any

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import DBusConnection, open_dbus_connection
from jeepney.wrappers import DBusErrorResponse, unwrap_msg

Ref = tuple[str, str]
"""(bus name, object path) of one accessible object."""

ACC = "org.a11y.atspi.Accessible"
COMP = "org.a11y.atspi.Component"
ACT = "org.a11y.atspi.Action"
TEXT = "org.a11y.atspi.Text"
ETEXT = "org.a11y.atspi.EditableText"
PROPS = "org.freedesktop.DBus.Properties"
ROOT: Ref = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")

# Atspi.StateType values (verified against libatspi 2.52).
STATE_NAMES = {
    1: "active", 4: "checked", 7: "editable", 8: "enabled", 10: "expanded", 11: "focusable",
    12: "focused", 20: "pressed", 23: "selected", 24: "sensitive", 25: "showing", 30: "visible",
    43: "read_only",
}  # fmt: skip
ACTIVE, FOCUSED, ICONIFIED, SHOWING, VISIBLE = 1, 12, 15, 25, 30


from workers.hands.errors import AtspiError as AtspiError  # noqa: E402 - re-exported for linux.py


def a11y_bus_address(session_address: str | None = None) -> str:
    session = open_dbus_connection(bus=session_address or "SESSION")
    try:
        bus = DBusAddress("/org/a11y/bus", bus_name="org.a11y.Bus", interface="org.a11y.Bus")
        reply = unwrap_msg(session.send_and_get_reply(new_method_call(bus, "GetAddress"), timeout=5))
        return str(reply[0])
    finally:
        session.close()


class AtspiClient:
    def __init__(self, session_address: str | None = None, timeout: float = 2.0) -> None:
        try:
            self._conn: DBusConnection = open_dbus_connection(bus=a11y_bus_address(session_address))
        except (OSError, DBusErrorResponse, KeyError) as exc:
            raise AtspiError(f"accessibility bus unavailable: {exc}") from exc
        self._timeout = timeout
        self._lock = threading.Lock()  # jeepney's blocking connection isn't thread-safe

    def close(self) -> None:
        self._conn.close()

    def call(
        self, ref: Ref, interface: str, method: str, signature: str | None = None, body: tuple[Any, ...] = ()
    ) -> Any:
        message = new_method_call(
            DBusAddress(ref[1], bus_name=ref[0], interface=interface), method, signature, body
        )
        try:
            with self._lock:
                reply = self._conn.send_and_get_reply(message, timeout=self._timeout)
            return unwrap_msg(reply)
        except (DBusErrorResponse, TimeoutError, OSError) as exc:
            raise AtspiError(f"{method} failed: {exc}") from exc

    def prop(self, ref: Ref, interface: str, name: str) -> Any:
        return self.call(ref, PROPS, "Get", "ss", (interface, name))[0][1]

    # ---- Accessible ------------------------------------------------------------

    def children(self, ref: Ref) -> list[Ref]:
        return [(str(b), str(p)) for b, p in self.call(ref, ACC, "GetChildren")[0]]

    def parent(self, ref: Ref) -> Ref:
        bus, path = self.prop(ref, ACC, "Parent")
        return (str(bus), str(path))

    def name(self, ref: Ref) -> str:
        return str(self.prop(ref, ACC, "Name") or "")

    def role(self, ref: Ref) -> str:
        return str(self.call(ref, ACC, "GetRoleName")[0])

    def states(self, ref: Ref) -> set[int]:
        words = self.call(ref, ACC, "GetState")[0]
        return {i * 32 + bit for i, word in enumerate(words) for bit in range(32) if word >> bit & 1}

    def interfaces(self, ref: Ref) -> set[str]:
        return {str(i) for i in self.call(ref, ACC, "GetInterfaces")[0]}

    # ---- Component / Action / Text ---------------------------------------------

    def extents(self, ref: Ref) -> tuple[int, int, int, int]:
        x, y, w, h = self.call(ref, COMP, "GetExtents", "u", (0,))[0]
        return int(x), int(y), int(w), int(h)

    def grab_focus(self, ref: Ref) -> bool:
        return bool(self.call(ref, COMP, "GrabFocus")[0])

    def action_names(self, ref: Ref) -> list[str]:
        count = int(self.prop(ref, ACT, "NActions"))
        return [str(self.call(ref, ACT, "GetName", "i", (i,))[0]) for i in range(min(count, 10))]

    def do_action(self, ref: Ref, index: int) -> bool:
        return bool(self.call(ref, ACT, "DoAction", "i", (index,))[0])

    def text(self, ref: Ref, limit: int = 20_000) -> str:
        count = int(self.prop(ref, TEXT, "CharacterCount"))
        return str(self.call(ref, TEXT, "GetText", "ii", (0, min(count, limit)))[0])

    def set_text(self, ref: Ref, value: str) -> bool:
        return bool(self.call(ref, ETEXT, "SetTextContents", "s", (value,))[0])

    def insert_text(self, ref: Ref, position: int, value: str) -> bool:
        return bool(self.call(ref, ETEXT, "InsertText", "isi", (position, value, len(value)))[0])
