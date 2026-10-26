"""The active-context snapshot (taken when the voice hotkey is pressed).

`build` is pure, so every awkward desktop (two monitors, mixed DPI, negative coordinates,
a window on another desktop, a blocklisted app in focus) is testable with fakes.
"""

import asyncio
import os
import re
import time
import uuid

from pydantic import Field

from contracts.common import RiskLevel
from contracts.context import ActiveContext, Display
from contracts.hands import ElementInfo, WindowInfo
from contracts.tools import ToolAssessment, ToolResult
from workers.common.base import Args
from workers.common.displays import display_at
from workers.hands.locate import element_meta, window_meta
from workers.hands.tools import HandsEnv, WorkerTool

MAX_SHARED = 4_000
SECRET_LIKE = re.compile(
    r"(\b(?:\d[ -]?){13,19}\b|sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY|\bpassword\s*[:=]|"
    r"\b[A-Za-z0-9+/_-]{40,}\b)",
    re.I,
)


def shareable(text: str, what: str, limits: list[str]) -> str:
    """Selected text / clipboard, unless it looks like a password, key or card number."""
    text = text.strip()
    if text and SECRET_LIKE.search(text):
        limits.append(f"the {what} looked like a password, key or card number, so it wasn't shared")
        return ""
    return text[:MAX_SHARED]


def window_display(window: WindowInfo, displays: tuple[Display, ...]) -> Display | None:
    if window.bounds is None:
        return None
    b = window.bounds
    return display_at(b.x + b.width // 2, b.y + b.height // 2, displays)


def build(
    *,
    displays: tuple[Display, ...],
    pointer: tuple[int, int] | None,
    pointer_reliable: bool,
    windows: list[WindowInfo],
    control: ElementInfo | None,
    selection: str,
    clipboard: str | None,
    blocked_app: str,
    now: float,
    context_id: str,
) -> ActiveContext:
    limits: list[str] = []
    focused = None if blocked_app else next((w for w in windows if w.focused), None)
    on = window_display(focused, displays) if focused else None
    if not displays:
        limits.append("couldn't list the displays")
    if pointer is not None and pointer_reliable:
        display = display_at(*pointer, displays)
    else:
        if displays:
            limits.append(
                "the pointer position isn't available here, so 'this screen' is the focused window's"
            )
        display = on or next((d for d in displays if d.primary), displays[0] if displays else None)
    if focused is not None and focused.bounds is None:
        limits.append("the focused window doesn't report its position")
    if focused is None and not blocked_app:
        limits.append("no focused window was found")
    if blocked_app:
        limits.append(f"{blocked_app} is on the blocklist: nothing of it is read or captured")
    return ActiveContext(
        context_id=context_id,
        taken_at=now,
        displays=displays,
        display=display,
        pointer=pointer if pointer_reliable else None,
        window=focused,
        window_display=on.display_id if on else "",
        windows=tuple(w for w in windows if not w.focused and w.on_screen)[:20],
        control=None if blocked_app else control,
        blocked_app=blocked_app,
        selection="" if blocked_app else shareable(selection, "selected text", limits),
        clipboard=None if clipboard is None else shareable(clipboard, "clipboard", limits),
        limits=tuple(limits),
    )


def pointer_reliable() -> bool:
    """On Wayland apps only see the pointer while it's over their own (X11) windows."""
    return not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland")


class ContextArgs(Args):
    clipboard: bool = Field(
        default=False, description="Include the clipboard (only when the user enabled it)."
    )


class ActiveContextTool(WorkerTool[ContextArgs]):
    """Internal: core calls it at the hotkey; never offered to the model."""

    name = "active_context"
    description = "Snapshot the display, window, control and selection the user is on."
    args_model = ContextArgs
    risk = RiskLevel.READ
    capabilities = ()

    def assess(self, args: ContextArgs, env: HandsEnv) -> ToolAssessment:
        return ToolAssessment(risk=self.risk, summary="Note what you're looking at")

    async def run(self, args: ContextArgs, env: HandsEnv) -> ToolResult:
        hands = env.hands
        windows = await asyncio.to_thread(hands.list_windows)
        blocked = await asyncio.to_thread(hands.focused_blocked_app)
        shown: list[WindowInfo] = []
        for w in windows:  # short ids, so tools can target the window directly
            label = f"{w.app} — {w.title}" if w.title else w.app
            short = env.ids.add("w", w.window_id, label, window_meta(w.app, w.title))
            shown.append(w.model_copy(update={"window_id": short}))
        control = None if blocked else await asyncio.to_thread(hands.focused_element)
        if control is not None:
            meta = element_meta(control.role, control.name, {})
            short = env.ids.add("e", control.element_id, f"{control.role} '{control.name}'", meta)
            control = control.model_copy(update={"element_id": short, "text": None})
        selection = "" if blocked else await asyncio.to_thread(hands.selected_text)
        clipboard = await asyncio.to_thread(env.clipboard) if args.clipboard else None
        context = build(
            displays=tuple(await asyncio.to_thread(env.displays)),
            pointer=await asyncio.to_thread(env.pointer),
            pointer_reliable=env.pointer_reliable(),
            windows=shown,
            control=control,
            selection=selection,
            clipboard=clipboard,
            blocked_app=blocked,
            now=time.time(),
            context_id=uuid.uuid4().hex[:10],
        )
        return ToolResult(
            ok=True, output=context.label(), evidence={"context": context.model_dump(mode="json")}
        )
