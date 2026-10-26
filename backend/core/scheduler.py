"""The automation loop: due routines start background tasks or reminders; watchers poll.

Runs inside the daemon (no OS scheduler needed). Routines missed while the daemon was off
are reported, never run late without the user saying so.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from contracts.automation import Routine, Schedule, Watcher
from core.automation_store import AutomationStore
from core.background import BackgroundManager
from core.notify import Notifier
from core.schedule import local_now, next_run, sentence
from core.watchers import WatcherService

log = logging.getLogger(__name__)
TICK_S = 5.0
MISSED_GRACE_S = 15 * 60
"""Late by more than this (daemon was off): report as missed instead of running."""


class Automations:
    def __init__(
        self,
        store: AutomationStore,
        background: BackgroundManager,
        notifier: Notifier,
        watchers: WatcherService,
        now: Callable[[], datetime] = local_now,
        paused: Callable[[], bool] = lambda: False,
    ) -> None:
        self._paused = paused
        self.store = store
        self._background = background
        self._notifier = notifier
        self._watchers = watchers
        self._now = now
        self._loop: asyncio.Task[None] | None = None
        self._busy: set[str] = set()

    def now(self) -> datetime:
        return self._now()

    # ---- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        await self.catch_up()
        self._loop = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            await asyncio.gather(self._loop, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("automation tick failed")
            await asyncio.sleep(TICK_S)

    async def tick(self) -> None:
        if self._paused():  # Pause holds routines and watchers too
            return
        await self.run_due_routines()
        await self.poll_watchers()

    # ---- routines --------------------------------------------------------------

    async def add_routine(
        self, name: str, prompt: str, schedule: Schedule, action: Literal["run", "notify"]
    ) -> Routine:
        upcoming = next_run(schedule, self._now())
        routine = Routine(
            routine_id=uuid.uuid4().hex[:12],
            name=name,
            prompt=prompt,
            schedule=schedule,
            action=action,
            created_at=time.time(),
            next_run_at=upcoming.timestamp() if upcoming else None,
        )
        await self.store.save_routine(routine)
        return routine

    async def catch_up(self) -> None:
        """At startup: anything that should have run while we were off is reported, not run."""
        now = self._now()
        for routine in await self.store.routines():
            if (
                routine.enabled
                and routine.next_run_at
                and routine.next_run_at < now.timestamp() - MISSED_GRACE_S
            ):
                when = datetime.fromtimestamp(routine.next_run_at).astimezone().strftime("%a %H:%M")
                upcoming = next_run(routine.schedule, now)
                await self.store.save_routine(
                    routine.model_copy(
                        update={
                            "last_status": f"missed {when}",
                            "next_run_at": upcoming.timestamp() if upcoming else None,
                            "enabled": upcoming is not None,
                        }
                    )
                )
                await self._notifier.notify(
                    f"Missed: {routine.name}",
                    f"It was due {when} while the app was off. Run it from Automations if you still want it.",
                    "routine",
                )

    async def run_due_routines(self) -> None:
        now = self._now()
        for routine in await self.store.routines():
            if routine.enabled and routine.next_run_at is not None and routine.next_run_at <= now.timestamp():
                await self.run_routine(routine, now)

    async def run_routine(self, routine: Routine, now: datetime | None = None, manual: bool = False) -> None:
        now = now or self._now()
        if routine.action == "notify":
            await self._notifier.notify(routine.name, routine.prompt, "reminder")
            status = "reminded"
        else:
            task = await self._background.submit(routine.prompt, origin=f"routine:{routine.routine_id}")
            status = f"started task {task.task_id[:6]}"
        if manual:
            await self.store.save_routine(
                routine.model_copy(update={"last_run_at": now.timestamp(), "last_status": status})
            )
            return
        upcoming = next_run(routine.schedule, now)
        await self.store.save_routine(
            routine.model_copy(
                update={
                    "last_run_at": now.timestamp(),
                    "last_status": status,
                    "next_run_at": upcoming.timestamp() if upcoming else None,
                    "enabled": upcoming is not None,
                }
            )
        )

    # ---- watchers --------------------------------------------------------------

    async def add_watcher(self, watcher: Watcher) -> Watcher:
        await self.store.save_watcher(watcher, {})
        return watcher

    async def poll_watchers(self) -> None:
        now = time.time()
        due = [
            (w, s)
            for w, s in await self.store.watchers()
            if w.status == "active"
            and w.watcher_id not in self._busy
            and (w.last_checked_at is None or now - w.last_checked_at >= w.every_seconds)
        ]
        await asyncio.gather(*(self._poll(w, s) for w, s in due))

    async def _poll(self, watcher: Watcher, state: dict[str, object]) -> None:
        self._busy.add(watcher.watcher_id)
        try:
            updated, new_state, fired = await self._watchers.check(watcher, state)
            await self.store.save_watcher(updated, new_state)
            if fired:
                await self._notifier.notify(updated.name, updated.last_detail, "watcher")
                if updated.action == "run" and updated.prompt:
                    await self._background.submit(updated.prompt, origin=f"watcher:{updated.watcher_id}")
        finally:
            self._busy.discard(watcher.watcher_id)


def describe_routine(routine: Routine) -> str:
    verb = "remind you:" if routine.action == "notify" else "do:"
    return f"{sentence(routine.schedule)}, I'll {verb} “{routine.prompt}”"
