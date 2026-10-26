"""The voice overlay's brain: turns voice and voice-turn events into what the overlay shows.

The chat window and the overlay are two surfaces on one session: the chat window owns
the microphone and the chat (voice turns land in its history like typed ones); the
overlay mirrors a voice request's progress over whatever app you're in, offers Stop and
inline Approve/Deny, then hides itself. All state lives here so it can be tested without
a window; `OverlayHost` (core/overlay_host.py) draws it in a separate process.
"""

import array
import asyncio
import base64
import binascii
import contextlib
import logging
import math
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from contracts.overlay import MAX_ITEMS, OverlayApproval, OverlayItem, OverlayView
from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatCancelled,
    ChatDelta,
    ChatDone,
    ChatError,
    ChatStarted,
    ChatThinking,
    DictationResult,
    ServerMessage,
    ToolFinished,
    ToolStarted,
    VoiceError,
    VoiceFinal,
    VoicePartial,
)
from core.approvals import ApprovalBroker

log = logging.getLogger(__name__)
HIDE_DONE_S = 4.0
HIDE_ERROR_S = 6.0
HIDE_QUICK_S = 1.5
HIDE_IDLE_S = 120.0
"""A finished conversation stays in the box (like a reply on screen) until the next one, a click
on its close button, or this long with nothing new."""
NEEDS_CHAT = "Open the chat window: it holds the microphone."
Hotkey = Literal["toggle_chat", "toggle_dictation", "stop", "approve", "deny"]
LEVEL_EVERY_S = 0.08
"""At most ~12 level updates a second: enough for a smooth orb, cheap on the pipe."""


class OverlaySurface(Protocol):
    async def show(self, view: OverlayView) -> None: ...

    async def hide(self) -> None: ...

    async def level(self, value: float) -> None: ...


class NullSurface:
    """No overlay (turned off, no display, or not started)."""

    async def show(self, view: OverlayView) -> None:
        pass

    async def hide(self) -> None:
        pass

    async def level(self, value: float) -> None:
        pass


def pcm_level(pcm_b64: str) -> float:
    """Loudness of a chunk of 16-bit little-endian PCM, 0-1 (RMS on a log scale:
    -60 dBFS and below is 0, full scale is 1)."""
    try:
        raw = base64.b64decode(pcm_b64, validate=True)
    except (binascii.Error, ValueError):
        return 0.0
    samples = array.array("h", raw[: len(raw) - len(raw) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768
    return 0.0 if rms <= 0.001 else min(1.0, 1 + math.log10(rms) / 3)


def _clip(text: str, limit: int = 800) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def one_line(text: str, limit: int = 160) -> str:
    line = " ".join(text.strip().split("\n", 1)[0].split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


class OverlayController:
    def __init__(
        self,
        approvals: ApprovalBroker,
        stop_all: Callable[[], Awaitable[object]],
        enabled: Callable[[], bool],
        hide_scale: float = 1.0,
        auto_send: Callable[[], bool] = lambda: False,
    ) -> None:
        self.surface: OverlaySurface = NullSurface()
        self._approvals = approvals
        self._stop_all = stop_all
        self._enabled = enabled
        self._scale = hide_scale
        self._auto_send = auto_send
        self._voice_turns: set[str] = set()
        self._reply: dict[str, str] = {}
        self._thread: list[OverlayItem] = []
        self.view: OverlayView | None = None
        self._hide_task: asyncio.Task[None] | None = None
        self._target = ""
        self.fallback: Callable[[str, str], Awaitable[None]] | None = None
        """Used when the overlay can't show (no display, Wayland limits): a desktop notification."""
        self.visible: Callable[[], bool] = lambda: True
        self._level_at = 0.0

    @property
    def listening(self) -> bool:
        return self.view is not None and self.view.phase == "listening"

    async def set_target(self, target: str) -> None:
        """Redraw with the target; leaves any pending auto-hide alone."""
        self._target = target
        if self.view is not None and self.view.phase != "needs_chat" and self._enabled():
            self.view = self.view.model_copy(update={"target": target})
            with contextlib.suppress(Exception):
                await self.surface.show(self.view)

    # ---- inputs ------------------------------------------------------------------

    async def audio(self, pcm_b64: str) -> None:
        """A microphone chunk from the chat window: pass its loudness on while listening."""
        if self.listening:
            await self.level(pcm_level(pcm_b64))

    async def level(self, value: float) -> None:
        """How loud the microphone is (0-1), at most about 12 times a second, while listening."""
        if not self.listening or not self._enabled():
            return
        now = time.monotonic()
        if now - self._level_at < LEVEL_EVERY_S:
            return
        self._level_at = now
        with contextlib.suppress(Exception):
            await self.surface.level(value)

    async def voice_show(
        self, phase: str, partial: str = "", status: str = "", hide_after: float | None = None
    ) -> None:
        """The hands-free loop's own states (it hears the user itself, not the chat window)."""
        view = OverlayView(phase=phase, partial=partial, status=status)
        await self._show(view, hide_after=hide_after)

    def voice_begin(self) -> None:
        """A new conversation: the box starts empty."""
        self._thread = []

    async def welcome(self, text: str, hide_after: float = 20.0) -> None:
        """First start: show the box with a hello, so you can see it's there."""
        self.voice_begin()
        self._add(OverlayItem(kind="answer", text=text))
        await self._show(OverlayView(phase="done"), hide_after=hide_after)

    async def voice_rest(self) -> None:
        """The conversation is over: keep it on screen for a while, then tidy away."""
        if self.view is not None and self._enabled():
            done = self.view.model_copy(
                update={"phase": "done", "partial": "", "status": "", "approval": None}
            )
            await self._show(done, hide_after=HIDE_IDLE_S)

    def _add(self, item: OverlayItem) -> None:
        self._thread = [*self._thread, item][-MAX_ITEMS:]

    def _step_done(self, call_id: str) -> None:
        self._thread = [i.model_copy(update={"done": True}) if i.ref == call_id else i for i in self._thread]

    async def voice_hide(self) -> None:
        self._cancel_hide()
        self.view = None
        with contextlib.suppress(Exception):
            await self.surface.hide()

    def track(self, request_id: str, heard: str = "") -> None:
        """A chat turn started from a voice transcript: mirror it."""
        self._voice_turns.add(request_id)
        if heard.strip():
            self._add(OverlayItem(kind="you", text=heard.strip()[:800]))

    async def hotkey(self, action: Hotkey, listeners: int) -> None:
        if action in ("approve", "deny"):
            await self.decide(None, action == "approve")
        elif action == "stop":  # the caller stops everything; just say so
            await self._show(OverlayView(phase="done", status="Stopped."), hide_after=HIDE_QUICK_S)
        elif listeners == 0:
            await self._show(OverlayView(phase="needs_chat", status=NEEDS_CHAT), hide_after=HIDE_ERROR_S)
        elif self.view is None or self.view.phase != "listening":
            await self._show(OverlayView(phase="listening", status="Listening…"))

    async def observe(self, msg: ServerMessage) -> None:
        """Every message any surface emits passes through here; most are ignored."""
        if isinstance(msg, VoicePartial):
            await self._show(OverlayView(phase="listening", partial=msg.text, status="Listening…"))
        elif isinstance(msg, VoiceFinal):
            if not msg.text.strip():
                await self._show(
                    OverlayView(phase="error", status="Didn't catch that."), hide_after=HIDE_QUICK_S * 2
                )
            elif msg.mode == "chat":
                await self._show(OverlayView(phase="thinking", partial=msg.text, status="Thinking…"))
            else:
                await self._show(
                    OverlayView(phase="thinking", partial=msg.text, status="Tidying your dictation…")
                )
        elif isinstance(msg, VoiceError):
            await self._show(
                OverlayView(phase="error", status=one_line(msg.message)), hide_after=HIDE_ERROR_S
            )
        elif isinstance(msg, DictationResult):
            status = "Typed into your app." if msg.inserted else (msg.note or "Added to the message box.")
            await self._show(
                OverlayView(phase="done", partial=msg.text, status=one_line(status)), hide_after=HIDE_DONE_S
            )
        elif isinstance(msg, ApprovalResolved):  # carries no request id: match the question shown
            if self.view and self.view.approval and self.view.approval.approval_id == msg.approval_id:
                answered = self.view.model_copy(update={"approval": None, "status": one_line(msg.reason)})
                await self._show(answered, keep_approval=False)
        elif getattr(msg, "request_id", None) in self._voice_turns:
            await self._turn_event(msg)

    async def _turn_event(self, msg: ServerMessage) -> None:
        heard = self.view.partial if self.view else ""
        if isinstance(msg, ChatStarted | ChatThinking):
            await self._show(OverlayView(phase="thinking", partial=heard, status="Thinking…"))
        elif isinstance(msg, ToolStarted):
            self._add(OverlayItem(kind="step", text=one_line(msg.summary), done=False, ref=msg.call_id))
            await self._show(OverlayView(phase="acting", partial=heard))
        elif isinstance(msg, ToolFinished):
            self._step_done(msg.call_id)
            if self.view is not None:
                await self._show(self.view)
        elif isinstance(msg, ApprovalRequested):
            approval = OverlayApproval(
                approval_id=msg.approval_id, tool=msg.tool, risk=msg.risk, summary=msg.summary
            )
            why = one_line(f"Asking because {msg.reason}.")
            await self._show(OverlayView(phase="acting", partial=heard, status=why, approval=approval))
        elif isinstance(msg, ChatDelta):
            text = self._reply.get(msg.request_id, "") + msg.text
            self._reply[msg.request_id] = text
            if self.view and self.view.approval is None:
                await self._show(self.view.model_copy(update={"status": one_line(text)}))
        elif isinstance(msg, ChatDone):
            reply = self._reply.pop(msg.request_id, "")
            self._voice_turns.discard(msg.request_id)
            self._add(OverlayItem(kind="answer", text=_clip(reply.strip() or "Done.")))
            await self._show(OverlayView(phase="done"), hide_after=HIDE_IDLE_S)
        elif isinstance(msg, ChatError) and msg.request_id:
            self._forget(msg.request_id)
            self._add(OverlayItem(kind="error", text=_clip(msg.message)))
            await self._show(OverlayView(phase="error"), hide_after=HIDE_IDLE_S)
        elif isinstance(msg, ChatCancelled):
            self._forget(msg.request_id)
            await self._show(OverlayView(phase="done", status="Stopped."), hide_after=HIDE_QUICK_S)

    # ---- actions from the overlay (or the approve/deny/stop shortcuts) ------------

    async def stop(self) -> None:
        await self._stop_all()
        await self._show(OverlayView(phase="done", status="Stopped."), hide_after=HIDE_QUICK_S)

    async def decide(self, approval_id: str | None, approved: bool) -> bool:
        """Answer the approval the overlay is showing (None: whichever is showing)."""
        shown = self.view.approval if self.view else None
        if shown is None or (approval_id is not None and approval_id != shown.approval_id):
            return False
        return self._approvals.resolve(shown.approval_id, approved, remember=False)

    async def dismiss(self) -> None:
        self._cancel_hide()
        self.view = None
        await self.surface.hide()

    # ---- internals -----------------------------------------------------------------

    def _forget(self, request_id: str) -> None:
        self._voice_turns.discard(request_id)
        self._reply.pop(request_id, None)

    async def _show(
        self, view: OverlayView, hide_after: float | None = None, keep_approval: bool = True
    ) -> None:
        if not self._enabled():
            return
        self._cancel_hide()
        pending = self.view.approval if self.view is not None else None
        if keep_approval and view.approval is None and pending is not None and view.phase == "acting":
            view = view.model_copy(update={"approval": pending})  # keep a pending question on screen
        if self._target and not view.target and view.phase not in ("needs_chat",):
            view = view.model_copy(update={"target": self._target})
        if not view.items and self._thread and view.phase != "needs_chat":
            view = view.model_copy(update={"items": list(self._thread)})
        if view.phase == "listening" and self._auto_send():
            view = view.model_copy(update={"auto_send": True})
        previous = self.view
        self.view = view
        if not self.visible() and self.fallback is not None:
            await self._notify_instead(previous, view)
        try:
            await self.surface.show(view)
        except Exception:
            log.exception("overlay show failed")
        if hide_after is not None:
            self._hide_task = asyncio.create_task(self._hide_later(hide_after * self._scale))

    async def _notify_instead(self, previous: OverlayView | None, view: OverlayView) -> None:
        """No overlay: a notification for the moments that matter (start, a question, the end)."""
        assert self.fallback is not None
        where = f" · {view.target}" if view.target else ""
        if view.phase == "listening" and (previous is None or previous.phase != "listening"):
            await self.fallback(f"Listening{where}", "Speak now; the reply appears in the chat window.")
        elif view.approval and (previous is None or previous.approval != view.approval):
            await self.fallback("Needs your OK", f"{view.approval.tool}: {view.approval.summary}"[:200])
        elif view.phase in ("done", "error", "needs_chat") and (
            previous is None or previous.phase != view.phase
        ):
            await self.fallback(
                {"done": "Done", "error": "Problem", "needs_chat": "Open the chat window"}[view.phase],
                view.status or "",
            )

    def _cancel_hide(self) -> None:
        if self._hide_task is not None and not self._hide_task.done():
            self._hide_task.cancel()
        self._hide_task = None

    async def _hide_later(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self.view = None
        with contextlib.suppress(Exception):
            await self.surface.hide()
