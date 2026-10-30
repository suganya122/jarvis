"""Watching a project while another AI works on it: no model calls, only the window.

Every POLL_S it finds the window (by part of its title) and looks for the control that shows only
while that AI is working (Claude and ChatGPT show "Stop"). Seen working and then gone twice in a
row: it has stopped, the user is told (aloud when Jarvis is free, and on screen), and Jarvis
checks the work (core.projects.review), which may send it back for fixes and watch again."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from contracts.common import Trust
from contracts.tools import ToolContext
from core.projects.store import WATCHED, Project, ProjectStore
from core.toolbox import ToolRegistry

log = logging.getLogger(__name__)
POLL_S = 20.0
DONE_AFTER = 2
"""Polls in a row without the working sign, after it was seen, before it counts as answered."""
NEVER_SEEN_AFTER = 6
"""Polls without ever seeing it work: it most likely answered before the first look."""
MISSING_AFTER = 3
GIVE_UP_S = 3 * 3600.0

Tell = Callable[[str, str], Awaitable[None]]
Review = Callable[[Project], Coroutine[Any, Any, bool]]


@dataclass(slots=True)
class Watch:
    seen_working: bool = False
    idle: int = 0
    missing: int = 0
    started: float = 0.0


def find_window(listing: str, wanted: str) -> str | None:
    """The short id of the first window whose 'App — Title' contains `wanted`."""
    needle = wanted.casefold().strip()
    for line in listing.splitlines():
        short, _, label = line.strip().partition("  ")
        if needle and short.startswith("w") and needle in label.casefold():
            return short
    return None


class ProjectWatcher:
    def __init__(
        self,
        store: ProjectStore,
        registry: ToolRegistry,
        tell: Tell,
        review: Review | None = None,
        rounds: int = 0,
        poll_s: float = POLL_S,
    ) -> None:
        self._store = store
        self._registry = registry
        self._tell = tell
        self._review = review
        self._rounds = rounds
        self._poll_s = poll_s
        self._watches: dict[str, Watch] = {}
        self._reviewing: dict[str, asyncio.Task[bool]] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        for task in [self._task, *self._reviewing.values()]:
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = None
        self._reviewing.clear()

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watching projects failed; trying again")
            await asyncio.sleep(self._poll_s)

    async def poll(self) -> None:
        """One look at every project another AI is working on, and the checks that are due."""
        active = await self._store.active()
        for project in active:
            if project.review_due and project.status not in WATCHED:
                self._start_review(project)
        watched = [p for p in active if p.status in WATCHED and p.watch_window]
        for gone in set(self._watches) - {p.id for p in watched}:
            del self._watches[gone]
        for project in watched:
            watch = self._watches.setdefault(project.id, Watch(started=time.time()))
            await self._look(project, watch)

    async def _run(self, tool: str, args: dict[str, object]) -> tuple[bool, str]:
        found = self._registry.get(tool)
        if found is None:
            return False, ""
        ctx = ToolContext(trace_id="projects", session_id="projects", trust=Trust.USER)
        result = await found.run(args, ctx)
        return result.ok, result.output

    async def _look(self, project: Project, watch: Watch) -> None:
        ok, listing = await self._run("list_windows", {})
        window = find_window(listing, project.watch_window) if ok else None
        if window is None:
            watch.missing += 1
            if watch.missing >= MISSING_AFTER:
                await self._finish(
                    project, "paused", f"I can't see the {project.built_with or 'AI'} window any more."
                )
            return
        watch.missing = 0
        working = False
        if project.working_marker:
            marker = project.working_marker.casefold()
            label = next((ln for ln in listing.splitlines() if ln.startswith(window + "  ")), "")
            found_ok, found = await self._run(
                "find_elements", {"window_id": window, "query": project.working_marker, "limit": 3}
            )
            working = marker in label.casefold() or (found_ok and not found.startswith("No matching"))
        if working:
            watch.seen_working, watch.idle = True, 0
            if project.status != "working":
                await self._store.update(
                    project, status="working", detail=f"{project.built_with or 'It'} is working on it."
                )
            return
        watch.idle += 1
        who = project.built_with or "It"
        if watch.seen_working and watch.idle >= DONE_AFTER:
            await self._finish(
                project, "answered", f"{who} has stopped on {project.name}: done, or asking you something."
            )
        elif not watch.seen_working and watch.idle >= NEVER_SEEN_AFTER:
            await self._finish(project, "answered", f"{who} seems to have answered on {project.name}.")
        elif time.time() - watch.started > GIVE_UP_S:
            await self._finish(
                project, "paused", f"{who} has been on {project.name} for hours; I stopped watching."
            )

    async def _finish(self, project: Project, status: str, said: str) -> None:
        checking = status == "answered" and self._review is not None and project.rounds < self._rounds
        if checking:
            said = f"{said.split(':')[0].rstrip('.')}. I'm checking the work now."
        await self._store.update(project, status=status, detail=said, review_due=checking)
        self._watches.pop(project.id, None)
        await self._tell(project.name, said)
        if checking:
            self._start_review(project)

    def _start_review(self, project: Project) -> None:
        running = self._reviewing.get(project.id)
        if self._review is None or (running is not None and not running.done()):
            return
        self._reviewing[project.id] = asyncio.create_task(self._review(project))
