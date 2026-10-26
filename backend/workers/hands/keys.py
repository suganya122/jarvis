"""Key-combo parsing to Linux evdev key codes (layout-independent physical keys)."""

KEYCODES: dict[str, int] = {
    "esc": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "leftbrace": 26, "rightbrace": 27, "enter": 28, "ctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    "semicolon": 39, "apostrophe": 40, "grave": 41, "shift": 42, "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "comma": 51, "dot": 52, "slash": 53, "alt": 56, "space": 57, "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65, "f8": 66, "f9": 67, "f10": 68,
    "f11": 87, "f12": 88, "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106,
    "end": 107, "down": 108, "pagedown": 109, "insert": 110, "delete": 111, "meta": 125,
}  # fmt: skip
ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "cmd": "meta", "command": "meta", "super": "meta", "win": "meta",
    "windows": "meta", "option": "alt", "opt": "alt", "return": "enter", "escape": "esc", "del": "delete",
    "pgup": "pageup", "pgdn": "pagedown", "pagedn": "pagedown", "bksp": "backspace", "-": "minus",
    "=": "equal", ",": "comma", ".": "dot", "/": "slash", ";": "semicolon", "'": "apostrophe",
    "`": "grave", "[": "leftbrace", "]": "rightbrace", "\\": "backslash", "arrowup": "up",
    "arrowdown": "down", "arrowleft": "left", "arrowright": "right", "spacebar": "space",
}  # fmt: skip
MODIFIERS = ("ctrl", "shift", "alt", "meta")
MAX_KEYS = 5


class KeyError_(ValueError):
    pass


def parse_combo(combo: str) -> list[str]:
    """'Ctrl+Shift+T' -> ['ctrl', 'shift', 't'], modifiers first, validated."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+")]
    if not combo.strip() or any(not p for p in parts):
        raise KeyError_(f"can't read the key combo '{combo}'")
    keys = [ALIASES.get(p, p) for p in parts]
    unknown = [k for k in keys if k not in KEYCODES]
    if unknown:
        raise KeyError_(f"unknown key '{unknown[0]}'")
    if len(keys) > MAX_KEYS or len(set(keys)) != len(keys):
        raise KeyError_("too many or repeated keys")
    mods = [k for k in MODIFIERS if k in keys]
    rest = [k for k in keys if k not in MODIFIERS]
    if len(rest) > 1:
        raise KeyError_("only one non-modifier key per combo")
    return mods + rest


def display(keys: list[str]) -> str:
    return "+".join(k.capitalize() if len(k) > 1 else k.upper() for k in keys)
