"""What Jarvis is working on right now: every request while it runs (typed, spoken, from a chat app
or in the background), so a new message never has to wait for one to finish. The conversation
reads it ("how's it going?"), adds to a request while it works, pauses, resumes or cancels it
(core.task_tools); the model sees the others in its context.

A request checks in between its steps (checkpoint): a pause holds it there and what the user added
reaches it there, so nothing is cut off mid-step. There is one screen, one mouse and one keyboard:
a request using them keeps them for its run of steps, and another waits its turn (ScreenLease).
"""

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Literal

from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatCancelled,
    ChatDelta,
    ChatDone,
    ChatError,
    ServerMessage,
    ToolFinished,
    ToolStarted,
)
from core.chat_types import Emit
from core.policy import FOCUS_STEALING_TOOLS

State = Literal["working", "waiting_approval", "waiting_screen", "paused", "done", "failed", "cancelled"]
STEPS_KEPT = 12
SAID_CHARS = 1_500
KEEP_FINISHED_S = 15 * 60.0
"""Finished requests stay listed this long, so "did it work?" can still be answered."""
SCREEN_TOOLS = FOCUS_STEALING_TOOLS | {"click_element", "type_text", "choose_menu", "arrange_windows"}
"""Steps that use the screen, mouse or keyboard: one request at a time."""


@dataclass
class Task:
    ref: str
    """'t3': short, to say and to type."""
    request_id: str
    trace_id: str
    text: str
    started: float
    background: bool
    runner: "asyncio.Task[object] | None"
    state: State = "working"
    steps: deque[str] = field(default_factory=lambda: deque(maxlen=STEPS_KEPT))
    count: int = 0
    now: str = ""
    """The step it's on (or the approval it's waiting for)."""
    said: str = ""
    """What it said since its last step: at the end, its answer."""
    notes: list[str] = field(default_factory=list)
    """What the user added while it works, for its next step."""
    going: asyncio.Event = field(default_factory=asyncio.Event)
    """Set unless paused."""
    finished: float | None = None
    waiting_for: str = ""

    def __post_init__(self) -> None:
        self.going.set()

    @property
    def live(self) -> bool:
        return self.finished is None

    def line(self, now: float) -> str:
        """One line about it, for the model."""
        took = _ago(now - self.started)
        if not self.live:
            said = f": said “{self.said.strip()[:200]}”" if self.said.strip() else ""
            ended = _ago(now - (self.finished or now))
            return f"- {self.ref}: “{self.text[:80]}”, {self.state} {ended} ago{said}"
        state = {
            "working": f"working for {took}",
            "waiting_approval": f"waiting for the user's OK ({took} in)",
            "waiting_screen": f"waiting for the screen, which {self.waiting_for} is using ({took} in)",
            "paused": f"paused ({took} in)",
        }.get(self.state, self.state)
        doing = f"; now: {self.now}" if self.now else (f"; last: {self.steps[-1]}" if self.steps else "")
        return f"- {self.ref}: “{self.text[:80]}”, {state}, {self.count} steps{doing}"


def _ago(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"


class ScreenLease:
    """One screen, one mouse, one keyboard. A request using them keeps them while it's mid-step
    (waiting for the user's OK on that step too) and for HOLD_S after, so its run of clicks isn't
    broken into; then, or once it's finished or paused, the next one in line gets them."""

    HOLD_S = 20.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.holder: str | None = None
        self._busy = False
        self._last = 0.0
        self._changed = asyncio.Condition()

    def free_for(self, owner: str) -> bool:
        if self.holder in (None, owner):
            return True
        return not self._busy and self._clock() - self._last >= self.HOLD_S

    @contextlib.asynccontextmanager
    async def use(self, owner: str) -> AsyncIterator[None]:
        async with self._changed:
            while not self.free_for(owner):
                wait = None if self._busy else max(0.05, self.HOLD_S - (self._clock() - self._last))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), wait)
            self.holder, self._busy = owner, True
        try:
            yield
        finally:
            self._busy, self._last = False, self._clock()
            await self._notify()

    async def release(self, owner: str) -> None:
        if self.holder == owner and not self._busy:
            self.holder = None
            await self._notify()

    async def _notify(self) -> None:
        async with self._changed:
            self._changed.notify_all()


class TaskBoard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tasks: dict[str, Task] = {}
        self._made = 0
        self.screen = ScreenLease(clock)

    # ---- the request's own side -----------------------------------------------------------

    def start(self, request_id: str, trace_id: str, text: str, background: bool = False) -> Task:
        self._forget_old()
        self._made += 1
        task = Task(
            ref=f"t{self._made}", request_id=request_id, trace_id=trace_id, text=" ".join(text.split()),
            started=self._clock(), background=background, runner=asyncio.current_task(),
        )  # fmt: skip
        self._tasks[request_id] = task
        return task

    def watch(self, task: Task, emit: Emit) -> Emit:
        """The request's events, read on their way out: they are its progress."""

        async def watched(msg: ServerMessage) -> None:
            self._observe(task, msg)
            await emit(msg)

        return watched

    async def checkpoint(self, request_id: str) -> str:
        """Between steps: wait out a pause (the screen is let go meanwhile), and take what the user
        added since the last step."""
        task = self._tasks.get(request_id)
        if task is None:
            return ""
        if not task.going.is_set():
            await self.screen.release(request_id)
            await task.going.wait()
            task.state = "working"
        notes, task.notes = task.notes, []
        return "\n".join(f"(The user, while you were working on this: “{n}”)" for n in notes)

    async def end(self, task: Task) -> None:
        if task.live:  # it ended without saying how (it crashed)
            task.finished, task.state = self._clock(), "failed"
        task.now = ""
        await self.screen.release(task.request_id)

    @contextlib.asynccontextmanager
    async def use_screen(self, request_id: str) -> AsyncIterator[None]:
        """A step that needs the screen: while another request has it, this one shows as waiting."""
        task = self._tasks.get(request_id)
        if task is not None and not self.screen.free_for(request_id):
            holder = self._tasks.get(self.screen.holder or "")
            task.state, task.waiting_for = "waiting_screen", holder.ref if holder else "another request"
        async with self.screen.use(request_id):
            if task is not None and task.state == "waiting_screen":
                task.state, task.waiting_for = "working", ""
            yield

    def _observe(self, task: Task, msg: ServerMessage) -> None:
        if isinstance(msg, ToolStarted):
            task.count, task.now, task.said = task.count + 1, msg.summary, ""
        elif isinstance(msg, ToolFinished):
            outcome = "" if msg.status == "ok" else f" ({msg.status}: {msg.preview[:120]})"
            task.steps.append(f"{task.now or 'a step'}{outcome}")
            task.now = ""
        elif isinstance(msg, ApprovalRequested):
            task.state, task.now = "waiting_approval", f"asking the user: {msg.summary}"
        elif isinstance(msg, ApprovalResolved):
            task.state = "working"
        elif isinstance(msg, ChatDelta):
            task.said = (task.said + msg.text)[-SAID_CHARS:]
        elif isinstance(msg, ChatDone | ChatError | ChatCancelled) and task.live:
            task.finished = self._clock()
            task.state = (
                "done"
                if isinstance(msg, ChatDone)
                else "failed"
                if isinstance(msg, ChatError)
                else "cancelled"
            )
            if isinstance(msg, ChatError):
                task.said = msg.message

    # ---- the conversation's side ----------------------------------------------------------

    def now(self) -> float:
        return self._clock()

    def get(self, request_id: str) -> Task | None:
        return self._tasks.get(request_id)

    def by_trace(self, trace_id: str) -> Task | None:
        return next((t for t in self._tasks.values() if t.trace_id == trace_id), None)

    def find(self, ref: str, but: str = "") -> Task | None:
        """'t3', '3', or words from the request ('the EB bill'); a running one before a finished one."""
        key = ref.strip().casefold().removeprefix("task").strip()
        tasks = sorted(
            (t for t in self._tasks.values() if t.request_id != but), key=lambda t: (t.live, t.started)
        )
        for task in reversed(tasks):
            if key in (task.ref, task.ref[1:]):
                return task
        words = [
            w for w in key.split() if len(w) > 2 and w not in ("the", "and", "for", "that", "this", "one")
        ]
        scored = [(sum(w in t.text.casefold() for w in words), t.live, t.started, t) for t in tasks]
        best = max(scored, default=None, key=lambda s: s[:3])
        return best[3] if best and best[0] else None

    def all(self, but: str = "") -> list[Task]:
        self._forget_old()
        return sorted((t for t in self._tasks.values() if t.request_id != but), key=lambda t: t.started)

    def summary(self, but: str) -> str:
        """For the model's context: what else is running (and what just finished)."""
        now, others = self._clock(), self.all(but)
        live = [t.line(now) for t in others if t.live]
        if not live:
            return ""
        done = [t.line(now) for t in others if not t.live][-3:]
        return "\n".join(
            [
                "Other requests you're working on right now (details: tasks; to add to, pause, resume or "
                "cancel one: manage_task):",
                *live,
                *(["Finished lately:", *done] if done else []),
            ]
        )

    def pause(self, task: Task) -> None:
        task.going.clear()
        task.state = "paused"

    def resume(self, task: Task) -> None:
        task.going.set()
        if task.state == "paused":
            task.state = "working"

    def add(self, task: Task, note: str) -> None:
        task.notes.append(note)

    def cancel(self, task: Task) -> bool:
        if task.runner is None or task.runner.done():
            return False
        task.going.set()
        task.runner.cancel()
        return True

    def cancel_all(self) -> list["asyncio.Task[object]"]:
        """The kill switch: every running request (their runners, to wait for)."""
        runners = [
            t.runner for t in self._tasks.values() if t.live and t.runner is not None and not t.runner.done()
        ]
        for task in self._tasks.values():
            task.going.set()
        for runner in runners:
            runner.cancel()
        return runners

    def _forget_old(self) -> None:
        now = self._clock()
        for key in [k for k, t in self._tasks.items() if t.finished and now - t.finished > KEEP_FINISHED_S]:
            del self._tasks[key]
