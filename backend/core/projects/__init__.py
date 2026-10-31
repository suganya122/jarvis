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

from contracts.worker import WorkerConfig
from core.projects.cc_tools import StartClaudeCode, TellClaudeCode
from core.projects.review import MAX_ROUNDS, Host, Reviewer, RuntimeHost
from core.projects.store import ProjectStore
from core.projects.tools import CheckProject, ProjectStatus, SaveProject, describe
from core.projects.watch import ProjectWatcher
from core.toolbox import ToolRegistry

HEADER = (
    "Projects the user is working on with you (kept until done; they can come back to any of them at "
    "any time, even after other requests in between, so carry on from where each one is):"
)
MAX_SHOWN = 12
MAX_CHARS = 6_000

ContextFn = Callable[[str], Awaitable[str]]
__all__ = ["Projects", "RuntimeHost"]


class Projects:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        registry: ToolRegistry,
        host: Host,
        worker_config: Callable[[], WorkerConfig] | None = None,
    ) -> None:
        """worker_config: its protected paths (keys, the keychain, Jarvis's data) are off limits to
        Claude Code too."""
        protected = (lambda: list(worker_config().denied_paths)) if worker_config else list
        self.store = ProjectStore(conn)
        reviewer = Reviewer(self.store, host)
        self.watcher = ProjectWatcher(
            self.store, registry, host.tell, reviewer.review, MAX_ROUNDS, host.track
        )
        watch = lambda: self.watcher.start()  # noqa: E731
        for tool in (
            SaveProject(self.store, watch),
            ProjectStatus(self.store),
            CheckProject(self.store, lambda project: self.watcher.check(project)),
            StartClaudeCode(self.store, protected, watch),
            TellClaudeCode(self.store, protected, watch),
        ):
            registry.register(tool)

    async def context(self) -> str:
        active = (await self.store.active())[:MAX_SHOWN]
        if not active:
            return ""
        each = MAX_CHARS // len(active)  # one project gets it all; several share it
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
