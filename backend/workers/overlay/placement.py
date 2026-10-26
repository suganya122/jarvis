"""Where the overlay opens: the top-left corner of the display the mouse pointer is on.

Monitor lists come from workers.common.displays (Win32, CoreGraphics, or xrandr). If none
is available, the caller falls back to the whole virtual screen.
"""

from dataclasses import dataclass

from workers.common.displays import displays

TOP_MARGIN = 48
LEFT_MARGIN = 24
EDGE = 8


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def distance(self, px: int, py: int) -> int:
        dx = max(self.x - px, 0, px - (self.x + self.w - 1))
        dy = max(self.y - py, 0, py - (self.y + self.h - 1))
        return dx * dx + dy * dy


def monitor_at(px: int, py: int, monitors: list[Rect]) -> Rect | None:
    if not monitors:
        return None
    for monitor in monitors:
        if monitor.contains(px, py):
            return monitor
    return min(monitors, key=lambda m: m.distance(px, py))  # pointer in a gap between displays


def place(monitor: Rect, width: int, height: int) -> tuple[int, int]:
    """Top-left of the monitor, under the top bar, kept fully on it."""
    x = monitor.x + LEFT_MARGIN
    y = monitor.y + min(TOP_MARGIN, max(monitor.h - height - EDGE, 0))
    x = max(monitor.x + EDGE, min(x, monitor.x + monitor.w - width - EDGE))
    return x, y


def monitors() -> list[Rect]:
    return [Rect(d.x, d.y, d.width, d.height) for d in displays()]
