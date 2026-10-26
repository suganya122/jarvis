"""Hands contract: window and UI control, implemented per OS in workers/hands.

Element-level actions (through the OS accessibility API) are preferred over raw
coordinates: they work on covered windows and other workspaces, and can be verified.
"""

from typing import Any, Literal, Protocol, runtime_checkable

from contracts.common import ContractModel


class Rect(ContractModel):
    x: int
    y: int
    width: int
    height: int


class WindowInfo(ContractModel):
    window_id: str
    app: str
    title: str
    bounds: Rect | None
    """Screen bounds when the platform reports them (Wayland often does not)."""
    display_id: str = ""
    focused: bool
    on_screen: bool = True
    """False when it's on another virtual desktop/space or minimized (it can't be captured)."""


class ElementInfo(ContractModel):
    element_id: str
    """Opaque handle, valid while the app runs."""
    role: str
    name: str
    text: str | None = None
    """Visible text of text widgets; never set for password fields."""
    states: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    bounds: Rect | None = None
    depth: int = 0


class ActionResult(ContractModel):
    ok: bool
    evidence: dict[str, Any] = {}
    """What was observed that proves (or disproves) the action happened."""
    state_after: dict[str, Any] = {}
    error: str | None = None


MouseButton = Literal["left", "right", "middle"]
ScrollDirection = Literal["up", "down", "left", "right"]
TextMode = Literal["replace", "append"]


@runtime_checkable
class Hands(Protocol):
    def list_windows(self) -> list[WindowInfo]: ...

    def focus(self, window_id: str) -> ActionResult: ...

    def find_elements(self, window_id: str, query: str = "", limit: int = 50) -> list[ElementInfo]: ...

    def describe(self, element_id: str) -> ElementInfo: ...

    def activate(self, element_id: str) -> ActionResult:
        """Press/click/toggle an element through accessibility (no pointer needed)."""
        ...

    def set_text(self, element_id: str, text: str, mode: TextMode = "replace") -> ActionResult: ...

    def key(self, window_id: str, combo: str) -> ActionResult:
        """Send a key combo only after confirming window_id is the active window."""
        ...

    def scroll(self, window_id: str, direction: ScrollDirection, amount: int) -> ActionResult: ...

    def menu(self, window_id: str, path: list[str]) -> ActionResult: ...

    def move_resize(self, window_id: str, bounds: Rect) -> ActionResult: ...

    def insert_text(self, text: str) -> ActionResult:
        """Type text at the caret of the focused field in the active window (dictation)."""
        ...

    def focused_element(self) -> ElementInfo | None:
        """The control with keyboard focus in the active window."""
        ...

    def selected_text(self) -> str:
        """Selected text in the focused control ('' for password fields)."""
        ...

    def focused_blocked_app(self) -> str:
        """The blocklisted app whose window is active ('' if none): it is never read."""
        ...

    def visible_blocked_apps(self) -> list[str]:
        """Blocklisted apps with a window currently shown; screen captures refuse while any are."""
        ...

    def click(self, x: int, y: int, button: MouseButton = "left") -> ActionResult:
        """Raw coordinates; the fallback when accessibility can't reach a control."""
        ...
