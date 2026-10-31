"""os_tools environment and tool base."""

import re
from pathlib import Path
from typing import ClassVar

from contracts.worker import WorkerConfig
from workers.common.base import Args, human_size, truncate
from workers.common.base import WorkerTool as _WorkerTool
from workers.os_tools.paths import PathGuard

WORKER_NAME = "os_tools"

__all__ = ["STARTS_BY_ITSELF", "WORKER_NAME", "Args", "Env", "WorkerTool", "human_size", "truncate"]

STARTS_BY_ITSELF = re.compile(
    r"\.(?:zshrc|zshenv|zprofile|zlogin|bashrc|bash_profile|profile)\b|Launch(?:Agents|Daemons)\b"
    r"|(?:^|[;&|(\s`])crontab\b(?!\s+-l\b)|\blaunchctl\s+(?:load|bootstrap|enable|submit)\b"
)
"""Shell startup files, launch agents and cron: what's put there runs later, by itself. Like a
routine, it asks after reading outside content (the step gets the schedule capability)."""


class Env:
    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self.paths = PathGuard(config.allowed_roots, config.denied_paths)

    def display(self, path: Path) -> str:
        return self.paths.display(path)


class WorkerTool[A: Args](_WorkerTool[A, Env]):
    source: ClassVar[str] = WORKER_NAME
