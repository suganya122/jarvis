"""Hands for macOS: other apps' windows and controls through Accessibility.

The work happens in Jarvis.app's own executable started as `Jarvis --hands` (Swift; see
core/macos/Hands.swift and HandsActions.swift): this process sends it one JSON request per line and
reads back one reply each. Running inside Jarvis.app means macOS asks once to allow "Jarvis" in
Privacy & Security → Accessibility, the same as for the microphone and speech recognition.
"""

import contextlib
import itertools
import json
import queue
import subprocess
import threading
from collections.abc import Iterable
from typing import IO, Any

from contracts.hands import (
    ActionResult,
    ElementInfo,
    MouseButton,
    Rect,
    ScrollDirection,
    TextMode,
    WindowInfo,
)
from workers.hands.errors import AtspiError
from workers.hands.keys import MODIFIERS, parse_combo
from workers.hands.mac_keys import MAC_KEYCODES

REPLY_TIMEOUT_S = 15.0


class MacHands:
    def __init__(self, helper: str, blocked_apps: Iterable[str] = ()) -> None:
        self._helper = helper
        self._start()
        self._blocked = [b.casefold() for b in blocked_apps if b]
        self._ids = itertools.count(1)
        self._waiting: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._asked = False
        self._window_apps: dict[str, str] = {}

    def _start(self) -> None:
        """(Re)start the helper: if it stopped (Jarvis.app restarted), the next request starts one."""
        self._proc = subprocess.Popen(  # noqa: S603 - our own app's executable
            [self._helper, "--hands"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        threading.Thread(target=self._read, args=(self._proc.stdout,), daemon=True).start()

    # ---- talking to the helper ---------------------------------------------------------

    def _read(self, stream: IO[bytes] | None) -> None:
        assert stream is not None
        for raw in stream:
            with contextlib.suppress(ValueError):
                reply: dict[str, Any] = json.loads(raw)
                box = self._waiting.pop(int(reply.get("id", 0)), None)
                if box is not None:
                    box.put(reply)

    def _call(self, op: str, **args: Any) -> Any:
        if self._proc.poll() is not None:
            self._start()  # handles from the old one are gone: list windows again
        if self._proc.stdin is None:
            raise AtspiError("Jarvis.app's accessibility helper couldn't start; restart Jarvis")
        request_id = next(self._ids)
        box: queue.Queue[dict[str, Any]] = queue.Queue()
        self._waiting[request_id] = box
        with self._lock:
            self._proc.stdin.write(json.dumps({"id": request_id, "op": op, **args}).encode() + b"\n")
            self._proc.stdin.flush()
        try:
            reply = box.get(timeout=REPLY_TIMEOUT_S)
        except queue.Empty:
            self._waiting.pop(request_id, None)
            raise AtspiError("the app didn't answer in time") from None
        if not reply.get("ok"):
            error = str(reply.get("error") or "it didn't work")
            if "Accessibility" in error and not self._asked:
                self._asked = True  # once: macOS shows its own "allow Jarvis" prompt
                with contextlib.suppress(AtspiError):
                    self._call("trusted", prompt=True)
            raise AtspiError(error)
        return reply.get("result")

    def trusted(self) -> bool:
        return bool((self._call("trusted", prompt=False) or {}).get("trusted"))

    def _is_blocked(self, app: str) -> bool:
        return any(b in app.casefold() for b in self._blocked)

    def _check_window(self, window_id: str) -> None:
        if self._is_blocked(self._window_apps.get(window_id, "")):
            raise AtspiError("that app is on the blocklist")

    @staticmethod
    def _action(raw: Any) -> ActionResult:
        raw = raw or {}
        return ActionResult(
            ok=bool(raw.get("ok")), evidence=raw.get("evidence") or {}, error=raw.get("error")
        )

    @staticmethod
    def _element(raw: dict[str, Any]) -> ElementInfo:
        bounds = raw.get("bounds")
        return ElementInfo(
            element_id=raw["element_id"],
            role=raw.get("role", ""),
            name=raw.get("name", ""),
            text=raw.get("text"),
            states=tuple(raw.get("states") or ()),
            actions=tuple(raw.get("actions") or ()),
            bounds=Rect(**bounds) if bounds else None,
            depth=int(raw.get("depth", 0)),
        )

    # ---- the Hands protocol -------------------------------------------------------------

    def list_windows(self) -> list[WindowInfo]:
        found = []
        for raw in self._call("windows") or []:
            self._window_apps[raw["window_id"]] = raw.get("app", "")
            if self._is_blocked(raw.get("app", "")):
                continue
            bounds = raw.get("bounds")
            found.append(
                WindowInfo(
                    window_id=raw["window_id"],
                    app=raw.get("app", ""),
                    title=raw.get("title", ""),
                    bounds=Rect(**bounds) if bounds else None,
                    focused=bool(raw.get("focused")),
                    on_screen=bool(raw.get("on_screen", True)),
                )
            )
        return found

    def focus(self, window_id: str) -> ActionResult:
        self._check_window(window_id)
        return self._action(self._call("focus", window_id=window_id))

    def find_elements(self, window_id: str, query: str = "", limit: int = 50) -> list[ElementInfo]:
        self._check_window(window_id)
        raw = self._call("elements", window_id=window_id, query=query, limit=limit) or []
        return [self._element(r) for r in raw]

    def describe(self, element_id: str) -> ElementInfo:
        return self._element(self._call("describe", element_id=element_id))

    def activate(self, element_id: str) -> ActionResult:
        return self._action(self._call("press", element_id=element_id))

    def set_text(self, element_id: str, text: str, mode: TextMode = "replace") -> ActionResult:
        return self._action(self._call("set_value", element_id=element_id, text=text, mode=mode))

    def key(self, window_id: str, combo: str) -> ActionResult:
        self._check_window(window_id)
        keys = parse_combo(combo)
        flags = [k for k in keys if k in MODIFIERS]
        rest = [k for k in keys if k not in MODIFIERS]
        if not rest or rest[0] not in MAC_KEYCODES:
            raise AtspiError(f"can't press '{combo}' on a Mac")
        return self._action(
            self._call("key", window_id=window_id, keycode=MAC_KEYCODES[rest[0]], flags=flags)
        )

    def scroll(self, window_id: str, direction: ScrollDirection, amount: int) -> ActionResult:
        self._check_window(window_id)
        lines = max(1, min(int(amount), 50))
        dx = {"left": lines, "right": -lines}.get(direction, 0)
        dy = {"up": lines, "down": -lines}.get(direction, 0)
        return self._action(self._call("scroll", window_id=window_id, dx=dx, dy=dy))

    def menu(self, window_id: str, path: list[str]) -> ActionResult:
        self._check_window(window_id)
        return self._action(self._call("menu", window_id=window_id, path=path))

    def move_resize(self, window_id: str, bounds: Rect) -> ActionResult:
        self._check_window(window_id)
        return self._action(self._call("move_resize", window_id=window_id, bounds=bounds.model_dump()))

    def insert_text(self, text: str) -> ActionResult:
        if blocked := self.focused_blocked_app():
            return ActionResult(ok=False, error=f"{blocked} is on the blocklist")
        return self._action(self._call("insert_text", text=text))

    def focused_element(self) -> ElementInfo | None:
        if self.focused_blocked_app():
            return None
        raw = self._call("focused")
        return self._element(raw) if raw else None

    def selected_text(self) -> str:
        return "" if self.focused_blocked_app() else str(self._call("selected_text") or "")

    def focused_blocked_app(self) -> str:
        app = str(self._call("front_app") or "")
        return app if self._is_blocked(app) else ""

    def visible_blocked_apps(self) -> list[str]:
        return [a for a in self._call("visible_apps") or [] if self._is_blocked(a)]

    def click(self, x: int, y: int, button: MouseButton = "left") -> ActionResult:
        if blocked := self.focused_blocked_app():
            return ActionResult(ok=False, error=f"{blocked} is on the blocklist")
        return self._action(self._call("click", x=x, y=y, button=button))
