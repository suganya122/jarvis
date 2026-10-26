"""Line-delimited JSON loop over stdin/stdout (see contracts/worker.py)."""

import asyncio
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

from pydantic import ValidationError

from contracts import CONTRACT_VERSION
from contracts.tools import ToolAssessment, ToolResult
from contracts.worker import (
    Assess,
    Assessed,
    Cancel,
    Configure,
    Failure,
    FromWorker,
    Invoke,
    Outcome,
    Ready,
    WorkerConfig,
    to_worker_adapter,
)
from workers.common.base import WorkerTool

Tools = dict[str, WorkerTool[Any, Any]]


class Server:
    def __init__(
        self,
        out: TextIO,
        tools: Tools,
        make_env: Callable[[WorkerConfig], Any],
        expected_errors: tuple[type[Exception], ...] = (),
    ) -> None:
        self._out = out
        self._lock = threading.Lock()
        self._tools = tools
        self._make_env = make_env
        self._expected = expected_errors
        self._env: Any = None
        self._running: dict[str, asyncio.Task[None]] = {}

    def write(self, message: FromWorker) -> None:
        with self._lock:
            self._out.write(message.model_dump_json() + "\n")
            self._out.flush()

    async def handle_line(self, line: str) -> None:
        try:
            msg = to_worker_adapter.validate_json(line)
        except ValidationError:
            print("worker: ignoring malformed message", file=sys.stderr)
            return
        if isinstance(msg, Configure):
            self._env = self._make_env(msg.config)
            infos = tuple(t.info() for t in self._tools.values())
            self.write(Ready(contract_version=CONTRACT_VERSION, tools=infos))
        elif isinstance(msg, Cancel):
            task = self._running.get(msg.id)
            if task is not None:
                task.cancel()
        elif isinstance(msg, Assess | Invoke):
            task = asyncio.create_task(self._call(msg))
            if isinstance(msg, Invoke):
                call_id = msg.id
                self._running[call_id] = task

                def forget(_task: asyncio.Task[None]) -> None:
                    self._running.pop(call_id, None)

                task.add_done_callback(forget)

    async def _call(self, msg: Assess | Invoke) -> None:
        tool = self._tools.get(msg.tool)
        env = self._env
        if tool is None or env is None:
            self.write(Failure(id=msg.id, error="unknown tool" if tool is None else "worker not configured"))
            return
        try:
            args = tool.args_model.model_validate(msg.args)
        except ValidationError as exc:
            error = "invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:3])
            if isinstance(msg, Assess):
                self.write(Assessed(id=msg.id, assessment=_error_assessment(tool, error)))
            else:
                self.write(Outcome(id=msg.id, result=ToolResult(ok=False, output=error)))
            return
        try:
            assessment = await asyncio.to_thread(tool.assess, args, env)
            if isinstance(msg, Assess):
                self.write(Assessed(id=msg.id, assessment=assessment))
                return
            if assessment.error:  # re-checked at run time: state may have changed since approval
                self.write(Outcome(id=msg.id, result=ToolResult(ok=False, output=assessment.error)))
                return
            result = await tool.run(args, env)
        except asyncio.CancelledError:
            self.write(Outcome(id=msg.id, result=ToolResult(ok=False, output="Cancelled.")))
            return
        except self._expected as exc:
            result = ToolResult(ok=False, output=str(exc))
        except Exception as exc:
            result = ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")
        if isinstance(msg, Assess):
            self.write(Assessed(id=msg.id, assessment=_error_assessment(tool, result.output)))
        else:
            self.write(Outcome(id=msg.id, result=result))


def _error_assessment(tool: WorkerTool[Any, Any], error: str) -> ToolAssessment:
    return ToolAssessment(risk=tool.risk, summary=tool.name, error=error)


async def serve(server_factory: Callable[[TextIO], Server]) -> None:
    # stdout carries the protocol only; anything printed elsewhere goes to stderr.
    protocol_out = sys.stdout
    sys.stdout = sys.stderr
    server = server_factory(protocol_out)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def pump() -> None:  # a thread works on every OS, unlike async stdin pipes on Windows
        for raw in sys.stdin.buffer:
            loop.call_soon_threadsafe(queue.put_nowait, raw.decode("utf-8", errors="replace"))
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=pump, daemon=True).start()
    while (line := await queue.get()) is not None:
        if line.strip():
            await server.handle_line(line)
