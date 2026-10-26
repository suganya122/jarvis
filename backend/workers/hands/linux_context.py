"""Active-context reads for Linux (AT-SPI): the focused control, the selected text, and
whether a blocklisted app is the one in focus. LinuxHands delegates to these."""

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from contracts.hands import ElementInfo
from workers.hands.atspi import ACTIVE, ETEXT, FOCUSED, ROOT, TEXT, Ref

if TYPE_CHECKING:
    from workers.hands.linux import LinuxHands

MAX_SELECTION = 4_000


def _try[T](fn: Callable[[], T], default: T) -> T:
    try:
        return fn()
    except Exception:
        return default


def _focused_ref(h: "LinuxHands") -> Ref | None:
    active = next((w for w in h.list_windows() if w.focused), None)
    if active is None:
        return None
    from workers.hands.linux import _ref

    for ref, _ in h._walk(_ref(active.window_id)):
        if FOCUSED in _try(partial(h._c.states, ref), set[int]()):
            return ref
    return None


def focused_element(h: "LinuxHands") -> ElementInfo | None:
    ref = _focused_ref(h)
    return h._safe_element(ref, 1) if ref is not None else None


def selected_text(h: "LinuxHands") -> str:
    ref = _focused_ref(h)
    if ref is None or _try(partial(h._c.role, ref), "") == "password text":
        return ""
    if ETEXT not in _try(partial(h._c.interfaces, ref), set[str]()):
        return ""
    span = _try(partial(h._c.call, ref, TEXT, "GetSelection", "i", (0,)), None)
    if not span or span[0] == span[1]:
        return ""
    start, end = sorted((int(span[0]), int(span[1])))
    return h._c.text(ref)[start:end][:MAX_SELECTION]


def focused_blocked_app(h: "LinuxHands") -> str:
    """The blocklisted app whose window is active, if any (list_windows hides those apps)."""
    for app in _try(partial(h._c.children, ROOT), list[Ref]()):
        name = _try(partial(h._c.name, app), "")
        if not h._is_blocked(name):
            continue
        for win in _try(partial(h._c.children, app), list[Ref]()):
            if ACTIVE in _try(partial(h._c.states, win), set[int]()):
                return name
    return ""
