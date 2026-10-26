"""In-memory Hands backend that mirrors the GTK fixture app. Used by CI and contract tests."""

from collections.abc import Callable
from dataclasses import dataclass, field

from contracts.hands import (
    ActionResult,
    ElementInfo,
    MouseButton,
    Rect,
    ScrollDirection,
    TextMode,
    WindowInfo,
)
from workers.hands.keys import parse_combo
from workers.hands.verify import Snapshot, describe_changes


class FakeError(Exception):
    pass


@dataclass
class Node:
    role: str
    name: str
    text: str | None = None
    states: set[str] = field(default_factory=lambda: {"enabled", "sensitive", "showing", "visible"})
    on_action: Callable[[], None] | None = None
    children: list["Node"] = field(default_factory=list)


@dataclass
class FakeWindow:
    window_id: str
    app: str
    title: str
    nodes: dict[str, Node]
    active: bool = False
    keys: list[str] = field(default_factory=list)
    bounds: Rect = field(default_factory=lambda: Rect(x=0, y=0, width=420, height=320))
    on_screen: bool = True
    """False: on another virtual desktop, or minimized."""
    focus: str | None = None
    """Element id of the focused control."""
    selection: str = ""


def fixture_window(window_id: str = "fake|w1", active: bool = True) -> FakeWindow:
    nodes: dict[str, Node] = {}
    status = Node("label", "Count: 0")
    count = [0]

    def increment() -> None:
        count[0] += 1
        status.name = f"Count: {count[0]}"

    def toggle(node: Node) -> Callable[[], None]:
        return lambda: node.states.symmetric_difference_update({"checked"})

    agree = Node("check box", "Agree")
    agree.on_action = toggle(agree)
    entries = [
        ("menu", Node("menu", "File")),
        ("hello", Node("menu item", "Say hello", on_action=lambda: setattr(status, "name", "Hello!"))),
        ("status", status),
        ("increment", Node("push button", "Increment", on_action=increment)),
        ("name", Node("text", "Name", text="", states={"editable", "enabled", "showing", "visible"})),
        (
            "password",
            Node("password text", "Password", text="hunter2", states={"editable", "showing", "visible"}),
        ),
        ("agree", agree),
        ("delete", Node("push button", "Delete all", on_action=lambda: setattr(status, "name", "Deleted"))),
        ("send", Node("push button", "Send report", on_action=lambda: None)),
    ]
    for key, node in entries:
        nodes[f"{window_id}/{key}"] = node
    return FakeWindow(
        window_id=window_id, app="gtk_fixture_app.py", title="Hands Fixture", nodes=nodes, active=active
    )


class FakeHands:
    def __init__(self, windows: list[FakeWindow] | None = None, blocked_apps: tuple[str, ...] = ()) -> None:
        self.windows = {w.window_id: w for w in (windows or [fixture_window()])}
        self._blocked = [b.casefold() for b in blocked_apps]

    def _window(self, window_id: str) -> FakeWindow:
        window = self.windows.get(window_id)
        if window is None:
            raise FakeError("unknown window")
        if any(b in window.app.casefold() for b in self._blocked):
            raise FakeError("that app is on the blocklist")
        return window

    def _node(self, element_id: str) -> tuple[FakeWindow, Node]:
        window = self._window(element_id.rsplit("/", 1)[0])
        node = window.nodes.get(element_id)
        if node is None:
            raise FakeError("unknown element")
        return window, node

    def _info(self, element_id: str, node: Node) -> ElementInfo:
        actions = ("click",) if node.on_action else (("activate",) if node.text is not None else ())
        text = None if node.role == "password text" else node.text
        return ElementInfo(element_id=element_id, role=node.role, name=node.name, text=text,
                           states=tuple(sorted(node.states)), actions=actions, depth=1)  # fmt: skip

    def _snapshot(self, window: FakeWindow) -> Snapshot:
        return {i: self._info(i, n) for i, n in window.nodes.items()}

    def list_windows(self) -> list[WindowInfo]:
        return [
            WindowInfo(
                window_id=w.window_id,
                app=w.app,
                title=w.title,
                bounds=w.bounds,
                focused=w.active,
                on_screen=w.on_screen,
            )
            for w in self.windows.values()
            if not any(b in w.app.casefold() for b in self._blocked)
        ]

    def find_elements(self, window_id: str, query: str = "", limit: int = 50) -> list[ElementInfo]:
        window = self._window(window_id)
        needle = query.casefold()
        infos = [self._info(i, n) for i, n in window.nodes.items() if n.role != "label" or needle]
        return [i for i in infos if needle in f"{i.name} {i.text or ''} {i.role}".casefold()][:limit]

    def describe(self, element_id: str) -> ElementInfo:
        _, node = self._node(element_id)
        return self._info(element_id, node)

    def activate(self, element_id: str) -> ActionResult:
        window, node = self._node(element_id)
        if node.on_action is None:
            return ActionResult(ok=False, error="that control can't be pressed through accessibility")
        before = self._snapshot(window)
        node.on_action()
        changes = describe_changes(before, self._snapshot(window))
        return ActionResult(
            ok=True, evidence={"action": "click", "changes": changes, "verified": bool(changes)}
        )

    def set_text(self, element_id: str, text: str, mode: TextMode = "replace") -> ActionResult:
        _, node = self._node(element_id)
        if node.role == "password text":
            return ActionResult(ok=False, error="I don't type into password fields")
        if node.text is None or "editable" not in node.states:
            return ActionResult(ok=False, error="that control doesn't accept text")
        node.text = text if mode == "replace" else node.text + text
        return ActionResult(ok=True, evidence={"verified": True, "text_after": node.text[:300]})

    def focus(self, window_id: str) -> ActionResult:
        target = self._window(window_id)
        for window in self.windows.values():
            window.active = window is target
        return ActionResult(ok=True, evidence={"active": True})

    def key(self, window_id: str, combo: str) -> ActionResult:
        keys = parse_combo(combo)
        window = self._window(window_id)
        if not window.active:
            raise FakeError("that window isn't the active one, so no keys were sent; focus it first")
        window.keys.append("+".join(keys))
        return ActionResult(ok=True, evidence={"keys": keys, "active_window_confirmed": True, "changes": []})

    def scroll(self, window_id: str, direction: ScrollDirection, amount: int) -> ActionResult:
        if not self._window(window_id).active:
            raise FakeError("that window isn't the active one, so no keys were sent; focus it first")
        return ActionResult(
            ok=True, evidence={"direction": direction, "amount": amount, "active_window_confirmed": True}
        )

    def menu(self, window_id: str, path: list[str]) -> ActionResult:
        window = self._window(window_id)
        before = self._snapshot(window)
        for step in path:
            node = next(
                (
                    n
                    for n in window.nodes.values()
                    if n.role.startswith("menu") and n.name.casefold() == step.casefold()
                ),
                None,
            )
            if node is None:
                return ActionResult(ok=False, error=f"no menu item called '{step}'")
            if node.on_action:
                node.on_action()
        changes = describe_changes(before, self._snapshot(window))
        return ActionResult(ok=True, evidence={"path": path, "changes": changes, "verified": bool(changes)})

    def insert_text(self, text: str) -> ActionResult:
        window = next((w for w in self.windows.values() if w.active), None)
        if window is None:
            return ActionResult(ok=False, error="no app window is active")
        node = next((n for n in window.nodes.values() if "focused" in n.states), None)
        if node is None or node.text is None:
            return ActionResult(ok=False, error=f"no text field is focused in {window.app}")
        if node.role == "password text":
            return ActionResult(ok=False, error="I don't type into password fields")
        node.text += text
        return ActionResult(ok=True, evidence={"verified": True, "app": window.app, "field": node.name})

    def _active(self) -> FakeWindow | None:
        return next((w for w in self.windows.values() if w.active), None)

    def focused_element(self) -> ElementInfo | None:
        window = self._active()
        if window is None or window.focus is None or self.focused_blocked_app():
            return None
        return self._info(window.focus, window.nodes[window.focus])

    def selected_text(self) -> str:
        window = self._active()
        if window is None or self.focused_blocked_app():
            return ""
        if window.focus and window.nodes[window.focus].role == "password text":
            return ""
        return window.selection

    def focused_blocked_app(self) -> str:
        window = self._active()
        if window is not None and any(b in window.app.casefold() for b in self._blocked):
            return window.app
        return ""

    def visible_blocked_apps(self) -> list[str]:
        return [w.app for w in self.windows.values() if any(b in w.app.casefold() for b in self._blocked)]

    def move_resize(self, window_id: str, bounds: Rect) -> ActionResult:
        return ActionResult(ok=False, error="moving windows isn't supported by this backend")

    def click(self, x: int, y: int, button: MouseButton = "left") -> ActionResult:
        return ActionResult(ok=False, error="coordinate clicks arrive with screen capture (milestone 5)")
