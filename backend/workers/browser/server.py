"""Tool table for the browser worker."""

import asyncio
from typing import Any, TextIO

from contracts.worker import WorkerConfig
from workers.browser.env import Env
from workers.browser.tools import ALL
from workers.common.base import WorkerTool
from workers.common.server import Server


def all_tools() -> dict[str, WorkerTool[Any, Any]]:
    return {t.name: t() for t in ALL}


class _Envs:
    """Settings changes reconfigure the worker: keep the open browser unless its settings changed."""

    current: Env | None = None

    @classmethod
    def make(cls, config: WorkerConfig) -> Env:
        old = cls.current
        if old is not None and old.config == config.browser:
            return old
        cls.current = Env(config)
        if old is not None:
            asyncio.get_running_loop().create_task(old.close())
        return cls.current


def make_server(out: TextIO) -> Server:
    return Server(out, all_tools(), _Envs.make)
