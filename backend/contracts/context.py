"""Active context: what the user was looking at when they pressed the voice hotkey.

"This", "here", "this screen", "this window" and "what I'm looking at" mean this snapshot.
It's taken once, at the hotkey, and pinned to the chat until the user redirects it, so a
task keeps targeting the same window even if the user switches away.
"""

from pydantic import Field

from contracts.common import ContractModel
from contracts.hands import ElementInfo, WindowInfo


class Display(ContractModel):
    display_id: str
    """Stable for the session: 'd1', 'd2', … ordered left to right, then top to bottom."""
    name: str
    """What the user calls it: 'Screen 1', 'Screen 2'."""
    x: int
    y: int
    width: int
    height: int
    """Desktop coordinates (can be negative: a display left of or above the primary one)."""
    scale: float = 1.0
    """Physical pixels per desktop unit (2.0 on a Retina / 200% display)."""
    primary: bool = False

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height

    def distance(self, px: int, py: int) -> int:
        dx = max(self.x - px, 0, px - (self.x + self.width - 1))
        dy = max(self.y - py, 0, py - (self.y + self.height - 1))
        return dx * dx + dy * dy


class ActiveContext(ContractModel):
    context_id: str
    taken_at: float
    displays: tuple[Display, ...] = ()
    display: Display | None = None
    """The display under the pointer (or holding the focused window)."""
    pointer: tuple[int, int] | None = None
    window: WindowInfo | None = None
    """The focused window, with its short id ('w3') so tools can target it."""
    window_display: str = ""
    """Display id the window is on ('' if unknown)."""
    windows: tuple[WindowInfo, ...] = ()
    """Other visible windows (for "no, the other one")."""
    control: ElementInfo | None = None
    """The focused control in that window (its text is never included for password fields)."""
    blocked_app: str = ""
    """Set when the focused window belongs to a blocklisted app: nothing of it is read."""
    selection: str = Field(default="", max_length=4_000)
    """Selected text in the focused app (untrusted)."""
    clipboard: str | None = Field(default=None, max_length=4_000)
    """Only when the user turned clipboard sharing on (untrusted)."""
    limits: tuple[str, ...] = ()
    """What couldn't be determined, in plain words (e.g. 'pointer position unknown on Wayland')."""

    def label(self) -> str:
        """For the overlay and the chat: 'Screen 2 · Chrome · Inbox - Gmail'."""
        parts = [self.display.name] if self.display else []
        if self.blocked_app:
            parts.append(f"{self.blocked_app} (never read)")
        elif self.window:
            parts += [self.window.app, self.window.title[:60]] if self.window.title else [self.window.app]
        return " · ".join(p for p in parts if p) or "this computer"
