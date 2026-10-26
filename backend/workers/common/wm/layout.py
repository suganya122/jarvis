"""Where windows go: named slots on a chosen screen, and which window an app name means.
Pure functions, shared by every backend."""

from typing import Literal

from . import Screen, Win

Slot = Literal[
    "left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right",
    "left-third", "center-third", "right-third", "left-two-thirds", "right-two-thirds",
    "center", "full",
]  # fmt: skip
ScreenPick = Literal["current", "primary", "largest", "smallest", "builtin", "external", "other", "left-most",
                     "right-most", "1", "2", "3", "4"]  # fmt: skip

FRACTIONS: dict[str, tuple[float, float, float, float]] = {
    "left": (0, 0, 1 / 2, 1),
    "right": (1 / 2, 0, 1 / 2, 1),
    "top": (0, 0, 1, 1 / 2),
    "bottom": (0, 1 / 2, 1, 1 / 2),
    "top-left": (0, 0, 1 / 2, 1 / 2),
    "top-right": (1 / 2, 0, 1 / 2, 1 / 2),
    "bottom-left": (0, 1 / 2, 1 / 2, 1 / 2),
    "bottom-right": (1 / 2, 1 / 2, 1 / 2, 1 / 2),
    "left-third": (0, 0, 1 / 3, 1),
    "center-third": (1 / 3, 0, 1 / 3, 1),
    "right-third": (2 / 3, 0, 1 / 3, 1),
    "left-two-thirds": (0, 0, 2 / 3, 1),
    "right-two-thirds": (1 / 3, 0, 2 / 3, 1),
    "center": (0.15, 0.1, 0.7, 0.8),
}

ALIASES: dict[str, tuple[str, ...]] = {
    "chrome": ("google-chrome", "chrome", "chromium"),
    "terminal": (
        "gnome-terminal",
        "terminal",
        "ptyxis",
        "konsole",
        "kitty",
        "alacritty",
        "iterm",
        "windowsterminal",
        "wezterm",
        "tilix",
        "xterm",
    ),
    "code": ("code", "visual studio code", "vscodium"),
    "vs code": ("code", "visual studio code", "vscodium"),
    "vscode": ("code", "visual studio code", "vscodium"),
    "files": ("nautilus", "org.gnome.nautilus", "finder", "explorer", "dolphin"),
    "browser": ("google-chrome", "chrome", "firefox", "chromium", "safari", "msedge", "brave"),
    "edge": ("msedge", "microsoft edge"),
}


def rect(slot: str, screen: Screen) -> tuple[int, int, int, int]:
    fx, fy, fw, fh = FRACTIONS[slot]
    x = screen.x + round(screen.width * fx)
    y = screen.y + round(screen.height * fy)
    return x, y, round(screen.width * fw), round(screen.height * fh)


def pick_screen(pick: str, screens: list[Screen], current: int = -1) -> Screen:
    """A screen from how people say it: "my big screen", "the laptop", "the other one", "2"."""
    if not screens:
        raise ValueError("no screens found")
    ordered = sorted(screens, key=lambda s: (s.x, s.y))
    by_index = {s.index: s for s in screens}
    primary = next((s for s in screens if s.primary), ordered[0])
    here = by_index.get(current, primary)
    if pick.isdigit():
        n = int(pick)
        if not 1 <= n <= len(ordered):
            raise ValueError(
                f"there's no screen {n} (there {'is' if len(ordered) == 1 else 'are'} {len(ordered)})"
            )
        return ordered[n - 1]
    if pick == "primary":
        return primary
    if pick == "largest":
        return max(screens, key=lambda s: s.area)
    if pick == "smallest":
        return min(screens, key=lambda s: s.area)
    if pick == "builtin":
        return next((s for s in screens if s.builtin), min(screens, key=lambda s: s.area))
    if pick == "external":
        others = [s for s in screens if not s.builtin] or screens
        return max(others, key=lambda s: s.area)
    if pick == "other":
        rest = [s for s in ordered if s.index != here.index]
        return rest[0] if rest else here
    if pick == "left-most":
        return ordered[0]
    if pick == "right-most":
        return ordered[-1]
    return here  # "current"


def _norm(text: str) -> str:
    return text.lower().replace("_", " ").replace(".", " ").strip()


def find_window(app: str, windows: list[Win]) -> Win | None:
    """The window an app name means: its focused window first, else its first one."""
    wanted = _norm(app)
    names = ALIASES.get(wanted, (wanted,))

    def matches(w: Win) -> bool:
        app_name, title = _norm(w.app), _norm(w.title)
        return any(n in app_name for n in names) or any(n in title for n in names if len(n) > 3)

    found = [w for w in windows if matches(w)]
    if not found:
        return None
    return next((w for w in found if w.focused), found[0])
