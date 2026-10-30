"""Hands worker state: short ids for the model, the backend, captures, overlay."""

import sys
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Literal

from contracts.capture import CaptureInfo, CaptureKind
from contracts.context import Display
from contracts.hands import Hands
from contracts.worker import WorkerConfig
from workers.common import clipboard, displays
from workers.hands import capture
from workers.hands.capture import RawCapture
from workers.hands.locate import ElementRef, WindowRef, element_meta, find_element, find_window, window_meta
from workers.hands.overlay import Overlay

MAX_IDS = 5000


def _pointer_reliable() -> bool:
    from workers.hands.context import pointer_reliable

    return pointer_reliable()


class HandsUnavailable(Exception):
    pass


class IdMap:
    """Short, model-friendly ids ('w1', 'e7') for long backend handles."""

    def __init__(self) -> None:
        self._short: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._by_real: dict[str, str] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._next = {"w": 0, "e": 0}

    def add(self, kind: Literal["w", "e"], real: str, label: str, meta: dict[str, Any] | None = None) -> str:
        short = self._by_real.get(real)
        if short is None:
            self._next[kind] += 1
            short = f"{kind}{self._next[kind]}"
            self._by_real[real] = short
        self._short[short] = (real, label)
        if meta is not None:
            self._meta[short] = meta
        self._short.move_to_end(short)
        while len(self._short) > MAX_IDS:
            old_short, (real_old, _) = self._short.popitem(last=False)
            self._by_real.pop(real_old, None)
            self._meta.pop(old_short, None)
        return short

    def meta(self, short: str) -> dict[str, Any]:
        return self._meta.get(short.strip(), {})

    def short_for(self, real: str) -> str | None:
        return self._by_real.get(real)

    def resolve(self, short: str, kind: Literal["w", "e"]) -> tuple[str, str]:
        found = self._short.get(short.strip())
        if found is None or not short.strip().startswith(kind):
            what = "window" if kind == "w" else "control"
            hint = "list_windows" if kind == "w" else "find_elements"
            raise HandsUnavailable(f"unknown {what} id '{short}'; call {hint} again")
        return found


class HandsEnv:
    def __init__(
        self,
        config: WorkerConfig,
        hands: Hands | None = None,
        grab: "Callable[[CaptureKind, tuple[int, int, int, int] | None], RawCapture] | None" = None,
    ) -> None:
        self.config = config
        self.ids = IdMap()
        self._hands = hands
        self.grab = grab or capture.grab
        self.overlay = Overlay()
        self.captures: OrderedDict[str, CaptureInfo] = OrderedDict()
        # The desktop as the OS reports it; tests swap in fakes.
        self.displays: Callable[[], list[Display]] = displays.displays
        self.pointer: Callable[[], tuple[int, int] | None] = displays.pointer
        self.pointer_reliable: Callable[[], bool] = _pointer_reliable
        self.clipboard: Callable[[], str | None] = clipboard.read
        self.set_clipboard: Callable[[str], bool] = clipboard.write

    def window(self, ref: WindowRef) -> tuple[str, str, str]:
        """(real id, label, short id) for a session id or a locator."""
        if isinstance(ref, str):
            real, label = self.ids.resolve(ref, "w")
            return real, label, ref.strip()
        w = find_window(self.hands.list_windows(), ref)
        label = f"{w.app} — {w.title}" if w.title else w.app
        short = self.ids.add("w", w.window_id, label, window_meta(w.app, w.title))
        return w.window_id, label, short

    def element(self, ref: ElementRef) -> tuple[str, str, str]:
        if isinstance(ref, str):
            real, label = self.ids.resolve(ref, "e")
            return real, label, ref.strip()
        wreal, wlabel, wshort = self.window(ref.window)
        e = find_element(self.hands.find_elements(wreal, ref.name, 50), ref)
        meta = element_meta(e.role, e.name, self.ids.meta(wshort))
        short = self.ids.add("e", e.element_id, f"{e.role} '{e.name}' in {wlabel}", meta)
        return e.element_id, f"{e.role} '{e.name}' in {wlabel}", short

    def target(self, short: str) -> dict[str, Any]:
        """Locator for what an action touched, so the skill learner can replay it later."""
        meta = self.ids.meta(short)
        if not meta:
            return {}
        if short.startswith("e"):
            return {"element": meta}
        return {"window": {"app": meta.get("app", "")}}

    def remember_capture(self, info: CaptureInfo) -> None:
        """Geometry only, for highlights; the image itself is never kept."""
        self.captures[info.capture_id] = info
        while len(self.captures) > 20:
            self.captures.popitem(last=False)

    @property
    def hands(self) -> Hands:
        if self._hands is None:
            if sys.platform == "darwin":
                if not self.config.mac_app:
                    raise HandsUnavailable("Jarvis.app isn't installed: run the installer again")
                from workers.hands.mac import MacHands

                self._hands = MacHands(self.config.mac_app, self.config.blocked_apps)
                return self._hands
            if not sys.platform.startswith("linux"):
                raise HandsUnavailable("window and control access isn't available on this system yet")
            from workers.hands.linux import LinuxHands

            self._hands = LinuxHands(self.config.blocked_apps)
        return self._hands
