"""Arrange windows: "Chrome on the left, Terminal on the right", "Code on my big screen".

Uses the window manager backends in workers/common/wm. Apps that aren't open yet are opened
first. Every placement is checked afterwards by reading the window's position back.
"""

import asyncio
import time
from collections.abc import Callable

from pydantic import Field

from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolResult
from workers.common.base import Args as _Args
from workers.common.wm import Screen, Win, WindowManager, WindowManagerError, detect
from workers.common.wm.layout import ScreenPick, Slot, find_window, pick_screen, rect
from workers.os_tools.app_tools import INDEX, launch
from workers.os_tools.apps import best_match
from workers.os_tools.base import Args, Env, WorkerTool

OPEN_WAIT_S = 8.0
TOLERANCE = 40
"""Pixels: apps may round to their own size steps (terminals snap to character cells)."""

SLOT_WORDS = {"full": "full screen", "center": "the middle"}


class Placement(_Args):
    app: str = Field(
        min_length=1, max_length=60, description="App name, e.g. 'Chrome', 'Terminal', 'VS Code'."
    )
    slot: Slot = Field(
        default="full",
        description="Where on the screen: left/right/top/bottom halves, quarters (top-left…), thirds "
        "(left-third, center-third, right-third, left-two-thirds, right-two-thirds), center, or full.",
    )
    screen: ScreenPick = Field(
        default="current",
        description="Which screen: current, primary, largest ('my big screen'), smallest, builtin "
        "('the laptop'), external, other, left-most, right-most, or a number counted left to right.",
    )


class ArrangeArgs(Args):
    placements: list[Placement] = Field(min_length=1, max_length=6)


def describe(p: Placement) -> str:
    where = SLOT_WORDS.get(p.slot, f"the {p.slot.replace('-', ' ')}")
    screen = (
        ""
        if p.screen == "current"
        else f" of the {p.screen} screen"
        if not p.screen.isdigit()
        else f" of screen {p.screen}"
    )
    return f"{p.app} on {where}{screen}"


def _screen_of(win: Win, screens: list[Screen]) -> int:
    if win.screen >= 0:
        return win.screen
    cx, cy = win.x + win.width // 2, win.y + win.height // 2
    for s in screens:
        if s.x <= cx < s.x + s.width and s.y <= cy < s.y + s.height:
            return s.index
    return -1


def _close(a: Win, x: int, y: int, w: int, h: int) -> bool:
    return all(abs(p - q) <= TOLERANCE for p, q in ((a.x, x), (a.y, y), (a.width, w), (a.height, h)))


def arrange(
    wm: WindowManager, placements: list[Placement], opener: Callable[[str], bool]
) -> tuple[list[str], bool]:
    """Do it. Returns (one line per placement, whether every placement was confirmed)."""
    windows, screens = wm.windows(), wm.screens()
    focused = next((w for w in windows if w.focused), None)
    current = _screen_of(focused, screens) if focused else -1
    lines: list[str] = []
    all_ok = True
    done: list[tuple[Placement, Win, tuple[int, int, int, int]]] = []
    for p in placements:
        win = find_window(p.app, windows)
        if win is None and opener(p.app):
            deadline = time.monotonic() + OPEN_WAIT_S
            while win is None and time.monotonic() < deadline:
                time.sleep(0.4)
                windows = wm.windows()
                win = find_window(p.app, windows)
        if win is None:
            lines.append(f"{p.app}: no open window and couldn't open it.")
            all_ok = False
            continue
        try:
            screen = pick_screen(p.screen, screens, current)
        except ValueError as exc:
            lines.append(f"{p.app}: {exc}.")
            all_ok = False
            continue
        target = (
            (screen.x, screen.y, screen.width, screen.height) if p.slot == "full" else rect(p.slot, screen)
        )
        if p.slot == "full":
            wm.maximize(win, screen)
        else:
            wm.place(win, *target)
        done.append((p, win, target))
    time.sleep(0.3)  # let the window manager finish moving
    after = {w.id: w for w in wm.windows()}
    for p, win, (x, y, w, h) in done:
        moved = after.get(win.id)
        ok = moved is not None and _close(moved, x, y, w, h)
        all_ok = all_ok and ok
        lines.append(
            f"Put {describe(p)}."
            + ("" if ok else " (It didn't land exactly there: the app may limit its size.)")
        )
    return lines, all_ok


def _open(app_name: str) -> bool:
    app, _ = best_match(app_name, INDEX.apps())
    if app is None:
        return False
    launch(app)
    return True


class ArrangeWindows(WorkerTool[ArrangeArgs]):
    name = "arrange_windows"
    description = (
        "Move and resize app windows: halves, quarters, thirds, centre or full screen, on any screen "
        "('Chrome on the left, Terminal on the right', 'Code on my big screen'). Opens apps that aren't open."
    )
    args_model = ArrangeArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: ArrangeArgs, env: Env) -> ToolAssessment:
        summary = "Put " + ", ".join(describe(p) for p in args.placements)
        try:
            detect()
        except WindowManagerError as exc:
            return ToolAssessment(risk=self.risk, summary=summary, error=str(exc))
        return ToolAssessment(risk=self.risk, summary=summary)

    async def run(self, args: ArrangeArgs, env: Env) -> ToolResult:
        try:
            wm = detect()
            lines, ok = await asyncio.to_thread(arrange, wm, args.placements, _open)
        except WindowManagerError as exc:
            return ToolResult(ok=False, output=f"Couldn't arrange windows: {exc}")
        return ToolResult(ok=True, output=" ".join(lines), evidence={"verified": ok, "backend": wm.name})
