"""Background jobs as chips in the overlay's corner (the box holds the conversation; big jobs
run beside it while you keep working). Finished ones stay a little while, then go."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from contracts.automation import TaskInfo
from contracts.overlay import OverlayTask

log = logging.getLogger(__name__)
KEEP_FINISHED_S = 30.0
MAX_CHIPS = 4
TITLE_WORDS = 7


def title(prompt: str) -> str:
    words = prompt.split()
    short = " ".join(words[:TITLE_WORDS])
    return (short + "…" if len(words) > TITLE_WORDS else short)[:80] or "Background task"


def chip(task: TaskInfo) -> OverlayTask:
    if task.status in ("queued", "running"):
        state, detail = "running", f"Working on it{f': {task.current_tool}' if task.current_tool else ''}"
    elif task.status == "waiting_approval":
        state, detail = "waiting", "Needs your OK"
    elif task.status == "done":
        last = task.preview.strip().splitlines()[-1:] or ["Done."]
        state, detail = "done", last[0]
    else:
        state, detail = "failed", task.error or task.status.capitalize()
    return OverlayTask(task_id=task.task_id, title=title(task.prompt), state=state, detail=detail[:160])


class TaskChips:
    def __init__(
        self,
        show: Callable[[list[OverlayTask]], Awaitable[None]],
        announce: Callable[[str], Awaitable[None]] | None = None,
        keep_s: float = KEEP_FINISHED_S,
    ) -> None:
        self._show = show
        self._announce = announce
        self._keep_s = keep_s
        self._chips: dict[str, OverlayTask] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    @property
    def chips(self) -> list[OverlayTask]:
        return list(self._chips.values())[-MAX_CHIPS:]

    async def update(self, task: TaskInfo) -> None:
        new = chip(task)
        before = self._chips.get(task.task_id)
        self._chips[task.task_id] = new
        finished = new.state in ("done", "failed") and (before is None or before.state != new.state)
        if finished:
            timer = self._timers.pop(task.task_id, None)
            if timer:
                timer.cancel()
            self._timers[task.task_id] = asyncio.get_running_loop().call_later(
                self._keep_s, lambda: asyncio.ensure_future(self._drop(task.task_id))
            )
            if self._announce is not None and before is not None:  # it was running here: say it's over
                verdict = "is done" if new.state == "done" else "didn't finish"
                with contextlib.suppress(Exception):
                    await self._announce(f"Your background task, {new.title.rstrip('…')}, {verdict}.")
        await self._send()

    async def _drop(self, task_id: str) -> None:
        self._chips.pop(task_id, None)
        self._timers.pop(task_id, None)
        await self._send()

    async def _send(self) -> None:
        try:
            await self._show(self.chips)
        except Exception:
            log.exception("couldn't show the task chips")
