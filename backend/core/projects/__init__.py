"""Projects: something the user is building or working out with another AI (Claude, Claude Code,
ChatGPT…), from the first idea to the answer.

    store   one row per project: the idea, what was understood by asking, the brief, where it went
    tools   save_project and project_status, for the model
    watch   while the other AI works, its window is checked in the background (no model calls), and
            the user is told when it stops
    review  then Jarvis checks the work and has it fixed and polished, in a few rounds

Unfinished projects are in the model's context on every request, so other requests in between,
or a new conversation, don't make it forget one.
"""

from collections.abc import Awaitable, Callable

import aiosqlite

from core.projects.review import MAX_ROUNDS, Host, Reviewer, RuntimeHost
from core.projects.store import ProjectStore
from core.projects.tools import ProjectStatus, SaveProject, describe
from core.projects.watch import ProjectWatcher
from core.toolbox import ToolRegistry

HEADER = (
    "Projects the user is working on with you (kept until done; they can come back to any of them at "
    "any time, even after other requests in between, so carry on from where each one is):"
)
MAX_SHOWN = 5
MAX_CHARS = 4_000

ContextFn = Callable[[str], Awaitable[str]]
__all__ = ["Projects", "RuntimeHost"]


class Projects:
    def __init__(self, conn: aiosqlite.Connection, registry: ToolRegistry, host: Host) -> None:
        self.store = ProjectStore(conn)
        reviewer = Reviewer(self.store, host)
        self.watcher = ProjectWatcher(self.store, registry, host.tell, reviewer.review, MAX_ROUNDS)
        for tool in (SaveProject(self.store, lambda: self.watcher.start()), ProjectStatus(self.store)):
            registry.register(tool)

    async def context(self) -> str:
        active = (await self.store.active())[:MAX_SHOWN]
        if not active:
            return ""
        each = MAX_CHARS // len(active)  # one project gets it all; five share it
        lines = [f"- {describe(p)}"[:each] for p in active]
        return HEADER + "\n" + "\n".join(lines)

    def around(self, context: ContextFn) -> ContextFn:
        """The model's context for a request, with the unfinished projects after it."""

        async def with_projects(request: str) -> str:
            parts = [await context(request), await self.context()]
            return "\n\n".join(p for p in parts if p)

        return with_projects

    def start(self) -> None:
        self.watcher.start()

    async def stop(self) -> None:
        await self.watcher.stop()
