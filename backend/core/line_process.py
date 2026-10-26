"""A child process that speaks one JSON object per line over stdin/stdout.

Shared by channel plugins and the voice overlay (workers have their own request/response
host). A blocking reader thread hands lines to the event loop, so this works with any
loop type. The child gets the scrubbed worker environment.
"""

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from core.worker_host import BACKEND_ROOT, worker_env

log = logging.getLogger(__name__)


class LineProcess:
    def __init__(
        self,
        name: str,
        module: str,
        adapter: TypeAdapter[Any],
        on_message: Callable[[Any], None],
        on_exit: Callable[[int | None], None],
        pythonpath: tuple[Path, ...] = (),
        cwd: Path = BACKEND_ROOT,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._module = module
        self._adapter = adapter
        self._on_message = on_message
        self._on_exit = on_exit
        self._pythonpath = (BACKEND_ROOT, *pythonpath)
        self._cwd = cwd
        self._extra_env = extra_env or {}
        self._proc: subprocess.Popen[bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._write_lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        env = {**worker_env(), **self._extra_env}
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(str(p) for p in self._pythonpath))
        self._proc = subprocess.Popen(  # noqa: S603, ASYNC220 - our own interpreter; spawning is fast
            [sys.executable, "-m", self._module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            cwd=self._cwd,
            env=env,
        )
        name = f"{self.name}-reader"
        threading.Thread(target=self._read, args=(self._proc,), daemon=True, name=name).start()

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(OSError):
            if proc.stdin:
                proc.stdin.close()
        try:
            await asyncio.to_thread(proc.wait, 3)
        except subprocess.TimeoutExpired:
            proc.kill()

    async def send(self, message: BaseModel) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return False
        data = (message.model_dump_json() + "\n").encode()

        def write() -> None:
            assert proc.stdin is not None
            with self._write_lock:
                proc.stdin.write(data)
                proc.stdin.flush()

        try:
            await asyncio.to_thread(write)
            return True
        except OSError:
            log.warning("%s pipe closed", self.name)
            return False

    def _read(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None and self._loop is not None
        for raw in proc.stdout:
            self._loop.call_soon_threadsafe(self._dispatch, raw)
        self._loop.call_soon_threadsafe(self._exited, proc)

    def _dispatch(self, raw: bytes) -> None:
        try:
            message = self._adapter.validate_json(raw)
        except ValidationError:
            log.warning("%s sent a malformed message", self.name)
            return
        self._on_message(message)

    def _exited(self, proc: subprocess.Popen[bytes]) -> None:
        if proc is self._proc:  # not an old process from before a restart, nor a deliberate stop
            self._on_exit(proc.poll())
