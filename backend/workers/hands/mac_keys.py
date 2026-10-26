"""macOS virtual key codes (physical keys, ANSI layout) for the names keys.parse_combo returns."""

MAC_KEYCODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "equal": 24, "9": 25, "7": 26, "minus": 27, "8": 28, "0": 29,
    "rightbrace": 30, "o": 31, "u": 32, "leftbrace": 33, "i": 34, "p": 35, "enter": 36, "l": 37,
    "j": 38, "apostrophe": 39, "k": 40, "semicolon": 41, "backslash": 42, "comma": 43, "slash": 44,
    "n": 45, "m": 46, "dot": 47, "tab": 48, "space": 49, "grave": 50, "backspace": 51, "esc": 53,
    "capslock": 57, "f5": 96, "f6": 97, "f7": 98, "f3": 99, "f8": 100, "f9": 101, "f11": 103,
    "f10": 109, "f12": 111, "insert": 114, "home": 115, "pageup": 116, "delete": 117, "f4": 118,
    "end": 119, "f2": 120, "pagedown": 121, "f1": 122, "left": 123, "right": 124, "down": 125,
    "up": 126,
}  # fmt: skip
"""Modifiers (ctrl, shift, alt = Option, meta = Command) travel as flags, not keys."""
