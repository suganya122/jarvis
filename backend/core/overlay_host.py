"""Runs the voice overlay window (workers/overlay) as its own process.

It needs no token and no network: core sends it what to show and it sends back button
presses, over its private pipes. If it can't show anything (no display, no Tk) it says so
once and stays idle; the chat window keeps working either way.
"""

import asyncio
import logging
import time
from collections.abc import Coroutine
from typing import Any

from contracts.overlay import (
    FromOverlay,
    OverlayDecide,
    OverlayDismiss,
    OverlayHide,
    OverlayLevel,
    OverlayReady,
    OverlayShow,
    OverlayStop,
    OverlayTask,
    OverlayTasks,
    OverlayView,
    from_overlay_adapter,
)
from core.line_process import LineProcess
from core.overlay import OverlayController

log = logging.getLogger(__name__)
MAX_RESTARTS_PER_HOUR = 3


class OverlayHost:
    def __init__(self, controller: OverlayController, extra_env: dict[str, str] | None = None) -> None:
        self._controller = controller
        adapter = from_overlay_adapter
        self._process = LineProcess(
            "overlay", "workers.overlay", adapter, self._dispatch, self._on_exit, extra_env=extra_env
        )
        self._restarts: list[float] = []
        self._stopping = False
        self._tasks: set[asyncio.Task[object]] = set()
        self.available: bool | None = None  # None until the window process reports
        self.detail = "starting"

    async def start(self) -> None:
        self._stopping = False
        await self._process.start()

    async def stop(self) -> None:
        self._stopping = True
        await self._process.stop()

    async def show(self, view: OverlayView) -> None:
        await self._process.send(OverlayShow(view=view))

    async def hide(self) -> None:
        await self._process.send(OverlayHide())

    async def level(self, value: float) -> None:
        await self._process.send(OverlayLevel(level=value))

    async def tasks(self, tasks: list[OverlayTask]) -> None:
        await self._process.send(OverlayTasks(tasks=tasks))

    def _dispatch(self, msg: FromOverlay) -> None:
        if isinstance(msg, OverlayReady):
            self.available, self.detail = msg.available, msg.detail
            log.info("voice overlay %s: %s", "ready" if msg.available else "unavailable", msg.detail)
            return
        if isinstance(msg, OverlayStop):
            self._spawn(self._controller.stop())
        elif isinstance(msg, OverlayDecide):
            self._spawn(self._controller.decide(msg.approval_id, msg.approved))
        elif isinstance(msg, OverlayDismiss):
            self._spawn(self._controller.dismiss())

    def _spawn(self, coro: Coroutine[Any, Any, object]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_exit(self, code: int | None) -> None:
        if self._stopping:
            return
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < 3600]
        if len(self._restarts) >= MAX_RESTARTS_PER_HOUR:
            self.available, self.detail = False, f"the overlay kept stopping (exit code {code})"
            log.warning("voice overlay disabled: it kept stopping")
            return
        self._restarts.append(now)
        log.warning("voice overlay exited with code %s; restarting", code)
        self._spawn(self.start())
