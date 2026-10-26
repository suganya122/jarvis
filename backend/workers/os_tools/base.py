"""os_tools environment and tool base."""

from pathlib import Path
from typing import ClassVar

from contracts.worker import WorkerConfig
from workers.common.base import Args, human_size, truncate
from workers.common.base import WorkerTool as _WorkerTool
from workers.os_tools.paths import PathGuard

WORKER_NAME = "os_tools"

__all__ = ["WORKER_NAME", "Args", "Env", "WorkerTool", "human_size", "truncate"]


class Env:
    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self.paths = PathGuard(config.allowed_roots, config.denied_paths)

    def display(self, path: Path) -> str:
        return self.paths.display(path)


class WorkerTool[A: Args](_WorkerTool[A, Env]):
    source: ClassVar[str] = WORKER_NAME
