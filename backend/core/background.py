"""Background tasks: whole turns that run detached from any browser tab.

Each has live status (task.update), its events go to every open tab (so approvals can be
answered anywhere), a hard token budget, and a done notification. After a crash or
restart, unfinished tasks come back as 'interrupted' and only resume when the user asks.
"""

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from functools import partial

from contracts.automation import TaskInfo
from contracts.model import ModelTier
from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatCancelled,
    ChatDelta,
    ChatDone,
    ChatError,
    ChatSend,
    ChatStarted,
    ServerMessage,
    TaskUpdate,
    ToolFinished,
    ToolStarted,
)
from core.automation_store import AutomationStore
from core.chat import ChatService
from core.config import Settings
from core.notify import Notifier

log = logging.getLogger(__name__)
PREVIEW_CHARS = 300
UPDATE_INTERVAL_S = 0.5
Broadcast = Callable[[ServerMessage], Awaitable[None]]


class _TaskEmitter:
    """Receives the turn's events: forwards them to every tab and keeps the task's status."""

    def __init__(self, manager: "BackgroundManager", task: TaskInfo) -> None:
        self._manager = manager
        self.task = task
        self._last_update = 0.0

    async def __call__(self, message: ServerMessage) -> None:
        await self._manager.broadcast(message)
        task, status_changed = self.task, False
        if isinstance(message, ChatStarted):
            task, status_changed = (
                task.model_copy(update={"session_id": message.session_id, "status": "running"}),
                True,
            )
        elif isinstance(message, ChatDelta):
            task = task.model_copy(update={"preview": (task.preview + message.text)[-PREVIEW_CHARS:]})
        elif isinstance(message, ToolStarted):
            preview = task.preview if not task.preview or task.preview.endswith("\n") else task.preview + "\n"
            task = task.model_copy(
                update={"current_tool": message.summary, "step": task.step + 1, "preview": preview}
            )
        elif isinstance(message, ToolFinished):
            task = task.model_copy(update={"current_tool": ""})
        elif isinstance(message, ApprovalRequested):
            task, status_changed = task.model_copy(update={"status": "waiting_approval"}), True
        elif isinstance(message, ApprovalResolved):
            task, status_changed = task.model_copy(update={"status": "running"}), True
        elif isinstance(message, ChatDone):
            u = message.usage
            tokens = u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens
            task = task.model_copy(update={"status": "done", "tokens": tokens, "cost_usd": message.cost_usd,
                                           "current_tool": "", "finished_at": time.time()})  # fmt: skip
            status_changed = True
        elif isinstance(message, ChatError):
            task = task.model_copy(
                update={"status": "failed", "error": message.message, "finished_at": time.time()}
            )
            status_changed = True
        elif isinstance(message, ChatCancelled):
            task = task.model_copy(update={"status": "cancelled", "finished_at": time.time()})
            status_changed = True
        self.task = task
        now = time.monotonic()
        if status_changed or now - self._last_update > UPDATE_INTERVAL_S:
            self._last_update = now
            await self._manager.publish(task, persist=status_changed)


class BackgroundManager:
    def __init__(
        self,
        store: AutomationStore,
        chat: ChatService,
        settings: Callable[[], Settings],
        broadcast: Broadcast,
        notifier: Notifier,
    ) -> None:
        self._store = store
        self._chat = chat
        self._settings = settings
        self.broadcast = broadcast
        self._notifier = notifier
        self._running: dict[str, asyncio.Task[None]] = {}
        self._queue: deque[tuple[TaskInfo, ModelTier | None, str]] = deque()

    async def publish(self, task: TaskInfo, persist: bool = True) -> None:
        if persist:
            await self._store.save_task(task)
        await self.broadcast(TaskUpdate(task=task))

    async def recover(self) -> None:
        interrupted = await self._store.interrupt_live_tasks()
        for task in interrupted:
            await self.broadcast(TaskUpdate(task=task))
        if interrupted:
            n = len(interrupted)
            await self._notifier.notify(f"{n} background task{'s' if n > 1 else ''} interrupted",
                                        "Open Tasks to resume or discard.", "task")  # fmt: skip

    async def submit(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        tier: ModelTier | None = None,
        origin: str = "user",
    ) -> TaskInfo:
        task = TaskInfo(task_id=uuid.uuid4().hex[:16], session_id=session_id, prompt=prompt, origin=origin,
                        status="queued", created_at=time.time())  # fmt: skip
        await self.publish(task)
        self._queue.append((task, tier, prompt))
        self._pump()
        return task

    def _pump(self) -> None:
        while self._queue and len(self._running) < self._settings().background.max_concurrent:
            task, tier, text = self._queue.popleft()
            runner = asyncio.create_task(self._run(task, tier, text))
            self._running[task.task_id] = runner
            runner.add_done_callback(partial(self._on_done, task.task_id))

    def _on_done(self, task_id: str, _runner: "asyncio.Task[None]") -> None:
        self._running.pop(task_id, None)
        self._pump()

    async def _run(self, task: TaskInfo, tier: ModelTier | None, text: str) -> None:
        emitter = _TaskEmitter(self, task.model_copy(update={"status": "running"}))
        send = ChatSend(request_id=task.task_id, session_id=task.session_id, text=text, tier=tier)
        try:
            await self.publish(emitter.task)
            await self._chat.run(send, emitter, background=True)
        except asyncio.CancelledError:  # cancelled before the turn started
            emitter.task = emitter.task.model_copy(update={"status": "cancelled", "finished_at": time.time()})
            await asyncio.shield(self.publish(emitter.task))
            raise
        except Exception:
            log.exception("background task %s crashed", task.task_id)
            emitter.task = emitter.task.model_copy(update={"status": "failed", "error": "internal error"})
        final = emitter.task
        if final.status in ("running", "waiting_approval"):
            final = final.model_copy(update={"status": "done", "finished_at": time.time()})
        await self.publish(final)
        if final.status in ("done", "failed"):
            title = "Background task finished" if final.status == "done" else "Background task failed"
            body = (final.error or final.preview or final.prompt)[-200:]
            await self._notifier.notify(title, body, "task", task_id=final.task_id)

    async def cancel(self, task_id: str) -> bool:
        runner = self._running.get(task_id)
        if runner is not None:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            return True
        for i, (queued, _, _) in enumerate(self._queue):
            if queued.task_id == task_id:
                del self._queue[i]
                await self.publish(
                    queued.model_copy(update={"status": "cancelled", "finished_at": time.time()})
                )
                return True
        return False

    async def resume(self, task_id: str) -> bool:
        """Only ever by the user's choice: continue an interrupted task in its own chat."""
        task = await self._store.task(task_id)
        if task is None or task.status != "interrupted":
            return False
        text = f"Continue this task where it stopped (some steps may already be done): {task.prompt}"
        revived = task.model_copy(update={"status": "queued", "error": "", "finished_at": None})
        await self.publish(revived)
        self._queue.append((revived, None, text))
        self._pump()
        return True

    async def discard(self, task_id: str) -> bool:
        task = await self._store.task(task_id)
        if task is None or task.status != "interrupted":
            return False
        await self.publish(task.model_copy(update={"status": "cancelled", "finished_at": time.time()}))
        return True

    async def cancel_all(self) -> int:
        """Kill switch: stop everything that's running and drop the queue."""
        queued = list(self._queue)
        self._queue.clear()
        for task, _, _ in queued:
            await self.publish(task.model_copy(update={"status": "cancelled", "finished_at": time.time()}))
        running = list(self._running.values())
        for runner in running:
            runner.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        return len(queued) + len(running)

    async def shutdown(self) -> None:
        for runner in list(self._running.values()):
            runner.cancel()
        await asyncio.gather(*self._running.values(), return_exceptions=True)
