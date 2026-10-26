"""Spawns and supervises a worker process; turns its tools into core Tool objects.

Core never imports worker code: it starts `python -m <module>` and speaks the line
protocol in contracts/worker.py. A blocking reader thread keeps this independent of the
event loop type (Windows selector loops can't run asyncio subprocesses).
"""

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from contracts import ENV_PREFIX
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from contracts.voice import TranscriptEvent
from contracts.worker import (
    Assess,
    Assessed,
    AudioChunk,
    AudioClose,
    AudioOpen,
    Cancel,
    Configure,
    Failure,
    Invoke,
    Outcome,
    Ready,
    ToWorker,
    Transcript,
    WorkerConfig,
    from_worker_adapter,
)

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[1]
SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "SSH_AUTH")
MAX_RESTARTS_PER_MINUTE = 3


class WorkerUnavailable(RuntimeError):
    pass


def worker_env() -> dict[str, str]:
    """The parent environment minus anything that looks like a secret."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ANTHROPIC_", ENV_PREFIX))
        and not any(m in k.upper() for m in SECRET_ENV_MARKERS)
    }
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"  # pipes in UTF-8 everywhere (Windows would use cp1252 and garble "…")
    return env


class WorkerHost:
    def __init__(
        self, name: str, module: str, call_timeout_s: float = 120.0, extra_env: dict[str, str] | None = None
    ) -> None:
        self.name = name
        self._module = module
        self._timeout = call_timeout_s
        self._extra_env = extra_env or {}
        self._proc: subprocess.Popen[bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._ready: asyncio.Future[Ready] | None = None
        self._write_lock = threading.Lock()
        self._config: WorkerConfig | None = None
        self._restarts: list[float] = []
        self.tools: tuple[ToolInfo, ...] = ()
        self.on_transcript: Callable[[TranscriptEvent], None] | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def start(self, config: WorkerConfig) -> tuple[ToolInfo, ...]:
        self._config = config
        self._loop = asyncio.get_running_loop()
        self._proc = subprocess.Popen(  # noqa: S603, ASYNC220 - our own interpreter; spawning is fast
            [sys.executable, "-m", self._module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # worker diagnostics go to our stderr
            cwd=BACKEND_ROOT,
            env={**worker_env(), **self._extra_env},
        )
        threading.Thread(
            target=self._read, args=(self._proc,), daemon=True, name=f"{self.name}-reader"
        ).start()
        return await self.configure(config)

    async def configure(self, config: WorkerConfig) -> tuple[ToolInfo, ...]:
        self._config = config
        assert self._loop is not None
        self._ready = self._loop.create_future()
        await self._send(Configure(config=config))
        ready = await asyncio.wait_for(self._ready, 15)
        self.tools = ready.tools
        return ready.tools

    async def audio_open(self, stream_id: str, sample_rate: int) -> None:
        await self._ensure_running()
        await self._send(AudioOpen(id=stream_id, sample_rate=sample_rate))

    async def audio_chunk(self, stream_id: str, pcm_b64: str) -> None:
        await self._send(AudioChunk(id=stream_id, pcm_b64=pcm_b64))

    async def audio_close(self, stream_id: str, abort: bool = False) -> None:
        await self._send(AudioClose(id=stream_id, abort=abort))

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

    async def assess(self, tool: str, args: dict[str, Any]) -> ToolAssessment:
        reply = await self._request(Assess(id=uuid.uuid4().hex, tool=tool, args=args))
        assert isinstance(reply, ToolAssessment)
        return reply

    async def invoke(self, tool: str, args: dict[str, Any]) -> ToolResult:
        call_id = uuid.uuid4().hex
        try:
            reply = await self._request(Invoke(id=call_id, tool=tool, args=args))
        except asyncio.CancelledError:
            with contextlib.suppress(WorkerUnavailable, OSError):
                await self._send(Cancel(id=call_id))
            raise
        assert isinstance(reply, ToolResult)
        return reply

    # ---- internals --------------------------------------------------------

    async def _ensure_running(self) -> None:
        if self.alive:
            return
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < 60]
        if self._config is None or len(self._restarts) >= MAX_RESTARTS_PER_MINUTE:
            raise WorkerUnavailable(f"{self.name} worker is not running")
        self._restarts.append(now)
        log.warning("restarting %s worker", self.name)
        await self.start(self._config)

    async def _request(self, message: Assess | Invoke) -> ToolAssessment | ToolResult:
        await self._ensure_running()
        assert self._loop is not None
        future: asyncio.Future[ToolAssessment | ToolResult] = self._loop.create_future()
        self._pending[message.id] = future
        try:
            await self._send(message)
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError as exc:
            raise WorkerUnavailable(f"{self.name} worker did not answer in time") from exc
        finally:
            self._pending.pop(message.id, None)

    async def _send(self, message: ToWorker) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise WorkerUnavailable(f"{self.name} worker is not running")
        data = (message.model_dump_json() + "\n").encode()

        def write() -> None:
            assert proc.stdin is not None
            with self._write_lock:
                proc.stdin.write(data)
                proc.stdin.flush()

        try:
            await asyncio.to_thread(write)
        except (BrokenPipeError, OSError) as exc:
            raise WorkerUnavailable(f"{self.name} worker pipe closed") from exc

    def _read(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None and self._loop is not None
        for raw in proc.stdout:
            self._loop.call_soon_threadsafe(self._dispatch, raw)
        self._loop.call_soon_threadsafe(self._on_exit, proc)

    def _dispatch(self, raw: bytes) -> None:
        try:
            msg = from_worker_adapter.validate_json(raw)
        except ValidationError:
            log.warning("%s worker sent a malformed message", self.name)
            return
        if isinstance(msg, Ready):
            if self._ready is not None and not self._ready.done():
                self._ready.set_result(msg)
            return
        if isinstance(msg, Transcript):
            if self.on_transcript is not None:
                self.on_transcript(msg.event)
            return
        future = self._pending.get(msg.id)
        if future is None or future.done():
            return
        if isinstance(msg, Assessed):
            future.set_result(msg.assessment)
        elif isinstance(msg, Outcome):
            future.set_result(msg.result)
        elif isinstance(msg, Failure):
            future.set_exception(WorkerUnavailable(msg.error))

    def _on_exit(self, proc: subprocess.Popen[bytes]) -> None:
        if self._proc is not None and proc is not self._proc:
            return  # an old process from before a restart
        if proc is self._proc:
            log.warning("%s worker exited with code %s", self.name, proc.poll())
        for future in self._pending.values():
            if not future.done():
                future.set_exception(WorkerUnavailable(f"{self.name} worker stopped"))
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(WorkerUnavailable(f"{self.name} worker failed to start"))


class RemoteTool:
    """A Tool whose implementation lives in a worker process."""

    def __init__(self, info: ToolInfo, host: WorkerHost) -> None:
        self.info = info
        self._host = host

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        try:
            return await self._host.assess(self.info.name, args)
        except WorkerUnavailable as exc:
            return ToolAssessment(risk=self.info.risk, summary=self.info.name, error=str(exc))

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            return await self._host.invoke(self.info.name, args)
        except WorkerUnavailable as exc:
            return ToolResult(ok=False, output=str(exc))
