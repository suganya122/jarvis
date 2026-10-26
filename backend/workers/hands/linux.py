"""Hands for Linux: AT-SPI for windows and controls, uinput for keys and scrolling."""

import time
from collections import deque
from collections.abc import Callable, Iterable
from functools import partial

from contracts.hands import (
    ActionResult,
    ElementInfo,
    MouseButton,
    Rect,
    ScrollDirection,
    TextMode,
    WindowInfo,
)
from workers.hands import linux_context, uinput
from workers.hands.atspi import (
    ACT,
    ACTIVE,
    COMP,
    ETEXT,
    FOCUSED,
    ICONIFIED,
    ROOT,
    SHOWING,
    STATE_NAMES,
    TEXT,
    VISIBLE,
    AtspiClient,
    AtspiError,
    Ref,
)
from workers.hands.keys import KEYCODES, parse_combo
from workers.hands.verify import Snapshot, describe_changes

WINDOW_ROLES = frozenset({"frame", "window", "dialog", "alert", "file chooser"})
TEXT_ROLES = frozenset(
    {"text", "entry", "password text", "terminal", "paragraph", "document text", "editbar"}
)
INTERACTIVE_ROLES = frozenset(
    {"push button", "toggle button", "check box", "radio button", "menu item", "check menu item",
     "radio menu item", "link", "combo box", "list item", "page tab", "spin button", "slider", "menu",
     "table cell", "tree item", "button", *TEXT_ROLES}
)  # fmt: skip
PREFERRED_ACTIONS = ("click", "press", "activate", "toggle", "jump", "open")
MAX_NODES = 3000
MAX_DEPTH = 40
POLL_SECONDS = 0.8


def _id(ref: Ref) -> str:
    return f"{ref[0]}|{ref[1]}"


def _ref(element_id: str) -> Ref:
    bus, sep, path = element_id.partition("|")
    if not sep or not path.startswith("/"):
        raise AtspiError("unknown element")
    return bus, path


def _try[T](fn: Callable[[], T], default: T) -> T:
    """Accessibility calls fail routinely (widgets vanish); treat that as 'unknown'."""
    try:
        return fn()
    except AtspiError:
        return default


def _rect(ext: tuple[int, int, int, int] | None) -> Rect | None:
    return Rect(x=ext[0], y=ext[1], width=ext[2], height=ext[3]) if ext else None


def _best_action(names: list[str]) -> int:
    return next((names.index(a) for a in PREFERRED_ACTIONS if a in names), 0)


class LinuxHands:
    def __init__(self, blocked_apps: Iterable[str] = (), session_address: str | None = None) -> None:
        self._c = AtspiClient(session_address)
        self._blocked = [b.casefold() for b in blocked_apps if b]
        self._input: uinput.VirtualInput | None = None
        self._app_names: dict[str, str] = {}

    # ---- helpers -------------------------------------------------------------------

    def _is_blocked(self, app_name: str) -> bool:
        return any(b in app_name.casefold() for b in self._blocked)

    def app_name(self, bus: str) -> str:
        if bus not in self._app_names:
            for app in self._c.children(ROOT):
                self._app_names[app[0]] = _try(partial(self._c.name, app), "")
        return self._app_names.get(bus, "")

    def _check_allowed(self, ref: Ref) -> None:
        if self._is_blocked(self.app_name(ref[0])):
            raise AtspiError("that app is on the blocklist")

    def _window_of(self, ref: Ref) -> Ref:
        current = ref
        for _ in range(MAX_DEPTH):
            if self._c.role(current) in WINDOW_ROLES:
                return current
            current = self._c.parent(current)
            if current == ROOT or current[1] == "/org/a11y/atspi/null":
                break
        raise AtspiError("element is not inside a window")

    def _walk(self, start: Ref, include_hidden: bool = False) -> Iterable[tuple[Ref, int]]:
        queue: deque[tuple[Ref, int]] = deque([(start, 0)])
        seen = 0
        while queue and seen < MAX_NODES:
            ref, depth = queue.popleft()
            seen += 1
            yield ref, depth
            if depth >= MAX_DEPTH:
                continue
            states = _try(partial(self._c.states, ref), set[int]())
            if not include_hidden and depth > 0 and SHOWING not in states:
                continue
            queue.extend((child, depth + 1) for child in _try(partial(self._c.children, ref), list[Ref]()))

    def _element(self, ref: Ref, depth: int) -> ElementInfo:
        c = self._c
        role = c.role(ref)
        interfaces = c.interfaces(ref)
        states = c.states(ref)
        text = None
        if role in TEXT_ROLES and TEXT in interfaces and role != "password text":
            text = _try(partial(c.text, ref, 500), "")
        actions = tuple(_try(partial(c.action_names, ref), list[str]())) if ACT in interfaces else ()
        bounds = _rect(_try(partial(c.extents, ref), None)) if COMP in interfaces else None
        return ElementInfo(
            element_id=_id(ref),
            role=role,
            name=c.name(ref),
            text=text,
            depth=depth,
            bounds=bounds,
            states=tuple(sorted(STATE_NAMES[s] for s in states if s in STATE_NAMES)),
            actions=actions,
        )

    def _safe_element(self, ref: Ref, depth: int) -> ElementInfo | None:
        return _try(partial(self._element, ref, depth), None)

    def _snapshot(self, window: Ref) -> Snapshot:
        snap: Snapshot = {}
        for ref, depth in self._walk(window):
            info = self._safe_element(ref, depth)
            if info is not None and (info.name or info.text):
                snap[info.element_id] = info
        return snap

    def _settle(self, window: Ref, before: Snapshot) -> list[str]:
        deadline = time.monotonic() + POLL_SECONDS
        while True:
            changes = describe_changes(before, self._snapshot(window))
            if changes or time.monotonic() > deadline:
                return changes
            time.sleep(0.1)

    def _input_device(self) -> uinput.VirtualInput:
        if problem := uinput.available():
            raise AtspiError(problem)
        if self._input is None:
            self._input = uinput.VirtualInput()
        return self._input

    def _require_active(self, window_id: str) -> Ref:
        window = _ref(window_id)
        self._check_allowed(window)
        if ACTIVE not in self._c.states(window):
            raise AtspiError("that window isn't the active one, so no keys were sent; focus it first")
        return window

    # ---- Hands contract ------------------------------------------------------------

    def list_windows(self) -> list[WindowInfo]:
        windows = []
        for app in self._c.children(ROOT):
            app_name = _try(partial(self._c.name, app), "")
            self._app_names[app[0]] = app_name
            if self._is_blocked(app_name):
                continue
            for win in _try(partial(self._c.children, app), list[Ref]()):
                role = _try(partial(self._c.role, win), "")
                states = _try(partial(self._c.states, win), set[int]())
                if role not in WINDOW_ROLES or VISIBLE not in states:
                    continue
                windows.append(
                    WindowInfo(
                        window_id=_id(win),
                        app=app_name,
                        title=_try(partial(self._c.name, win), ""),
                        bounds=_rect(_try(partial(self._c.extents, win), None)),
                        focused=ACTIVE in states,
                        on_screen=SHOWING in states,
                    )
                )
        return windows

    def find_elements(self, window_id: str, query: str = "", limit: int = 50) -> list[ElementInfo]:
        window = _ref(window_id)
        self._check_allowed(window)
        needle = query.casefold().strip()
        found = []
        for ref, depth in self._walk(window):
            info = self._safe_element(ref, depth) if depth else None
            if info is None:
                continue
            interesting = info.role in INTERACTIVE_ROLES or bool(info.actions)
            hay = f"{info.name} {info.text or ''} {info.role}".casefold()
            if (interesting or (needle and info.name)) and (not needle or needle in hay):
                found.append(info)
                if len(found) >= limit:
                    break
        return found

    def describe(self, element_id: str) -> ElementInfo:
        ref = _ref(element_id)
        self._check_allowed(ref)
        return self._element(ref, 0)

    def activate(self, element_id: str) -> ActionResult:
        ref = _ref(element_id)
        self._check_allowed(ref)
        names = self._c.action_names(ref) if ACT in self._c.interfaces(ref) else []
        if not names:
            return ActionResult(ok=False, error="that control can't be pressed through accessibility")
        index = _best_action(names)
        window = self._window_of(ref)
        before = self._snapshot(window)
        done = self._c.do_action(ref, index)
        changes = self._settle(window, before)
        evidence = {"action": names[index], "changes": changes, "verified": bool(changes)}
        return ActionResult(ok=done, evidence=evidence, error=None if done else "the app refused the action")

    def set_text(self, element_id: str, text: str, mode: TextMode = "replace") -> ActionResult:
        ref = _ref(element_id)
        self._check_allowed(ref)
        if self._c.role(ref) == "password text":
            return ActionResult(ok=False, error="I don't type into password fields")
        if ETEXT not in self._c.interfaces(ref):
            return ActionResult(ok=False, error="that control doesn't accept text")
        if mode == "replace":
            done, expected = self._c.set_text(ref, text), text
        else:
            current = self._c.text(ref)
            done, expected = self._c.insert_text(ref, len(current), text), current + text
        after = self._c.text(ref)
        verified = after == expected
        error = None if verified else "the text didn't come out as expected"
        return ActionResult(
            ok=done and verified, evidence={"verified": verified, "text_after": after[:300]}, error=error
        )

    def focus(self, window_id: str) -> ActionResult:
        window = _ref(window_id)
        self._check_allowed(window)
        if ACTIVE in self._c.states(window):
            return ActionResult(ok=True, evidence={"already_active": True})
        targets = [window, *(r for r, d in self._walk(window) if d > 0)][:200]
        for ref in targets:
            focusable = COMP in _try(partial(self._c.interfaces, ref), set[str]())
            if focusable and _try(partial(self._c.grab_focus, ref), False):
                break
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if ACTIVE in self._c.states(window):
                return ActionResult(ok=True, evidence={"active": True})
            time.sleep(0.1)
        error = "the desktop didn't switch to that window (Wayland may block this); click it once"
        return ActionResult(ok=False, evidence={"active": False}, error=error)

    def key(self, window_id: str, combo: str) -> ActionResult:
        keys = parse_combo(combo)
        window = self._require_active(window_id)
        before = self._snapshot(window)
        self._input_device().send(uinput.combo_events([KEYCODES[k] for k in keys]))
        changes = self._settle(window, before)
        return ActionResult(
            ok=True, evidence={"keys": keys, "active_window_confirmed": True, "changes": changes}
        )

    def scroll(self, window_id: str, direction: ScrollDirection, amount: int) -> ActionResult:
        self._require_active(window_id)
        self._input_device().send(uinput.wheel_events(direction, amount))
        evidence = {"direction": direction, "amount": amount, "active_window_confirmed": True}
        return ActionResult(ok=True, evidence=evidence)

    def _menu_item(self, window: Ref, name: str) -> Ref | None:
        target = name.casefold().strip()
        for ref, depth in self._walk(window, include_hidden=True):
            role = _try(partial(self._c.role, ref), "")
            is_menu = depth > 0 and (role.endswith("menu item") or role == "menu")
            if is_menu and _try(partial(self._c.name, ref), "").casefold().replace("_", "") == target:
                return ref
        return None

    def menu(self, window_id: str, path: list[str]) -> ActionResult:
        window = _ref(window_id)
        self._check_allowed(window)
        before = self._snapshot(window)
        for step in path:
            item = self._menu_item(window, step)
            if item is None:
                return ActionResult(ok=False, error=f"no menu item called '{step}'")
            self._c.do_action(item, _best_action(self._c.action_names(item)))
            time.sleep(0.2)
        changes = self._settle(window, before)
        return ActionResult(ok=True, evidence={"path": path, "changes": changes, "verified": bool(changes)})

    def insert_text(self, text: str) -> ActionResult:
        active = next((w for w in self.list_windows() if w.focused), None)
        if active is None:
            return ActionResult(ok=False, error="no app window is active")
        window = _ref(active.window_id)
        for ref, _ in self._walk(window):
            states = _try(partial(self._c.states, ref), set[int]())
            if FOCUSED not in states:
                continue
            if self._c.role(ref) == "password text":
                return ActionResult(ok=False, error="I don't type into password fields")
            if ETEXT not in self._c.interfaces(ref):
                continue
            before = self._c.text(ref)
            caret = int(_try(partial(self._c.prop, ref, TEXT, "CaretOffset"), len(before)))
            caret = caret if 0 <= caret <= len(before) else len(before)
            self._c.insert_text(ref, caret, text)
            after = self._c.text(ref)
            verified = after == before[:caret] + text + before[caret:]
            evidence = {"verified": verified, "app": active.app, "field": self._c.name(ref)}
            return ActionResult(
                ok=verified, evidence=evidence, error=None if verified else "the text didn't land"
            )
        return ActionResult(ok=False, error=f"no text field is focused in {active.app}")

    def focused_element(self) -> ElementInfo | None:
        return linux_context.focused_element(self)

    def selected_text(self) -> str:
        return linux_context.selected_text(self)

    def focused_blocked_app(self) -> str:
        return linux_context.focused_blocked_app(self)

    def visible_blocked_apps(self) -> list[str]:
        shown = []
        for app in self._c.children(ROOT):
            name = _try(partial(self._c.name, app), "")
            if not self._is_blocked(name):
                continue
            for win in _try(partial(self._c.children, app), list[Ref]()):
                states = _try(partial(self._c.states, win), set[int]())
                if SHOWING in states and VISIBLE in states and ICONIFIED not in states:
                    shown.append(name)
                    break
        return shown

    def move_resize(self, window_id: str, bounds: Rect) -> ActionResult:
        return ActionResult(ok=False, error="moving windows isn't possible from apps on GNOME Wayland")

    def click(self, x: int, y: int, button: MouseButton = "left") -> ActionResult:
        return ActionResult(ok=False, error="coordinate clicks arrive with screen capture (milestone 5)")
