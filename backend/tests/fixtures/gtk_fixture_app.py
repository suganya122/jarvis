"""A small GTK 3 window the hands tests drive. Run with the system python3 (needs python3-gi).

Every control has a visible, checkable effect so actions can be verified.
"""

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

TITLE = "Hands Fixture"


def build() -> Gtk.Window:
    window = Gtk.Window(title=TITLE)
    window.set_default_size(420, 320)
    window.connect("destroy", Gtk.main_quit)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    window.add(box)

    menubar = Gtk.MenuBar()
    file_item = Gtk.MenuItem(label="File")
    file_menu = Gtk.Menu()
    hello = Gtk.MenuItem(label="Say hello")
    file_menu.append(hello)
    file_item.set_submenu(file_menu)
    menubar.append(file_item)
    box.pack_start(menubar, False, False, 0)

    status = Gtk.Label(label="Count: 0")
    box.pack_start(status, False, False, 0)
    count = [0]

    def increment(_button: Gtk.Button) -> None:
        count[0] += 1
        status.set_text(f"Count: {count[0]}")

    button = Gtk.Button(label="Increment")
    button.connect("clicked", increment)
    box.pack_start(button, False, False, 0)
    hello.connect("activate", lambda _i: status.set_text("Hello!"))

    name = Gtk.Entry()
    name.get_accessible().set_name("Name")
    box.pack_start(name, False, False, 0)

    password = Gtk.Entry(visibility=False)
    password.set_text("hunter2")
    password.get_accessible().set_name("Password")
    box.pack_start(password, False, False, 0)

    box.pack_start(Gtk.CheckButton(label="Agree"), False, False, 0)
    delete = Gtk.Button(label="Delete all")
    delete.connect("clicked", lambda _b: status.set_text("Deleted"))
    box.pack_start(delete, False, False, 0)
    box.pack_start(Gtk.Button(label="Send report"), False, False, 0)
    return window


def main() -> None:
    window = build()
    window.show_all()
    GLib.timeout_add(300, lambda: print("READY", flush=True) and False)
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
