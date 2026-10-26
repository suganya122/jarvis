"""Window arrangement: slots, picking a screen from how people say it, which window an app
name means, the X11 parser, and the whole arrange step against a fake window manager."""

import pytest

from workers.common.wm import Screen, Win
from workers.common.wm.layout import find_window, pick_screen, rect
from workers.common.wm.x11 import clip, parse_wmctrl
from workers.os_tools.window_tools import Placement, arrange, describe

LAPTOP = Screen(index=0, x=0, y=32, width=1440, height=868, primary=True, builtin=True, name="Built-in")
MONITOR = Screen(index=1, x=1440, y=0, width=2560, height=1400)


def test_slots_split_the_work_area() -> None:
    assert rect("left", LAPTOP) == (0, 32, 720, 868)
    assert rect("right", LAPTOP) == (720, 32, 720, 868)
    assert rect("top-right", MONITOR) == (2720, 0, 1280, 700)
    assert rect("right-third", MONITOR) == (3147, 0, 853, 1400)
    assert rect("left-two-thirds", MONITOR) == (1440, 0, 1707, 1400)


@pytest.mark.parametrize(
    ("pick", "index"),
    [("largest", 1), ("smallest", 0), ("builtin", 0), ("external", 1), ("primary", 0), ("1", 0), ("2", 1),
     ("other", 1), ("current", 0), ("right-most", 1)],
)  # fmt: skip
def test_screens_by_how_people_say_it(pick: str, index: int) -> None:
    assert pick_screen(pick, [MONITOR, LAPTOP], current=0).index == index


def test_a_screen_that_does_not_exist_is_explained() -> None:
    with pytest.raises(ValueError, match="no screen 3"):
        pick_screen("3", [LAPTOP, MONITOR])


WINDOWS = [
    Win(id="1", app="Google-chrome", title="Inbox - Gmail", x=0, y=0, width=800, height=600),
    Win(id="2", app="org.gnome.Terminal", title="srinith@pc: ~", x=0, y=0, width=800, height=600),
    Win(id="3", app="Code", title="app.py - jarvis - Visual Studio Code", x=0, y=0, width=800, height=600),
    Win(id="4", app="Google-chrome", title="YouTube", x=0, y=0, width=800, height=600, focused=True),
]


@pytest.mark.parametrize(
    ("app", "win_id"), [("Chrome", "4"), ("terminal", "2"), ("VS Code", "3"), ("code", "3")]
)
def test_app_names_find_their_windows(app: str, win_id: str) -> None:
    found = find_window(app, WINDOWS)
    assert found is not None and found.id == win_id  # Chrome: its focused window first


def test_an_app_with_no_window_is_none() -> None:
    assert find_window("Spotify", WINDOWS) is None


def test_wmctrl_listing_and_work_area() -> None:
    text = (
        "0x03a00003  0 120 80 960 1040 google-chrome.Google-chrome  pc Inbox - Gmail\n"
        "0x01e00001 -1 0 0 1920 32 gnome-panel.Gnome-panel  pc Top Panel\n"
    )
    wins = parse_wmctrl(text)
    assert [(w.id, w.app, w.title, w.x, w.width) for w in wins] == [
        ("0x03a00003", "Google-chrome", "Inbox - Gmail", 120, 960)
    ]
    assert clip((0, 0, 1920, 1080), (0, 32, 3840, 1048)) == (0, 32, 1920, 1048)


class FakeWM:
    name = "fake"

    def __init__(self) -> None:
        self.wins = {w.id: w for w in WINDOWS}
        self.calls: list[str] = []
        self.stubborn: set[str] = set()

    def windows(self) -> list[Win]:
        return list(self.wins.values())

    def screens(self) -> list[Screen]:
        return [LAPTOP, MONITOR]

    def place(self, win: Win, x: int, y: int, width: int, height: int) -> None:
        self.calls.append(f"place {win.app} {x},{y} {width}x{height}")
        if win.id not in self.stubborn:
            self.wins[win.id] = Win(win.id, win.app, win.title, x, y, width, height)

    def maximize(self, win: Win, screen: Screen) -> None:
        self.calls.append(f"maximize {win.app} on {screen.index}")
        self.wins[win.id] = Win(win.id, win.app, win.title, screen.x, screen.y, screen.width, screen.height)

    def focus(self, win: Win) -> None:
        return None


def test_arranging_places_each_window_and_checks_it() -> None:
    wm = FakeWM()
    lines, ok = arrange(wm, [Placement(app="Chrome", slot="left", screen="builtin"),
                             Placement(app="Terminal", slot="right", screen="builtin"),
                             Placement(app="Code", screen="largest")], lambda _a: False)  # fmt: skip
    assert ok
    assert wm.calls == [
        "place Google-chrome 0,32 720x868",
        "place org.gnome.Terminal 720,32 720x868",
        "maximize Code on 1",
    ]
    assert lines[0] == "Put Chrome on the left of the builtin screen."


def test_a_window_that_did_not_move_is_reported() -> None:
    wm = FakeWM()
    wm.stubborn.add("2")
    lines, ok = arrange(wm, [Placement(app="Terminal", slot="left")], lambda _a: False)
    assert not ok and "didn't land exactly there" in lines[0]


def test_an_app_that_is_not_open_is_opened_or_explained() -> None:
    opened: list[str] = []
    lines, ok = arrange(
        FakeWM(), [Placement(app="Spotify", slot="right")], lambda a: opened.append(a) or False
    )
    assert opened == ["Spotify"] and not ok and "couldn't open it" in lines[0]


def test_descriptions_read_naturally() -> None:
    assert (
        describe(Placement(app="Chrome", slot="top-left", screen="2")) == "Chrome on the top left of screen 2"
    )
    assert (
        describe(Placement(app="Code", slot="full", screen="largest"))
        == "Code on full screen of the largest screen"
    )
