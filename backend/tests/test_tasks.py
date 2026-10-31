"""Always responsive: a new message is answered while a long request works, and the conversation can
see it, add to it, pause, resume and cancel it (core.tasks, core.task_tools). Over the WebSocket,
with each request following its own script so they can run side by side."""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.common import RiskLevel
from contracts.model import (
    Completed,
    ModelEvent,
    ModelRequest,
    ProviderStatus,
    Role,
    TextDelta,
    ToolCallRequested,
    Usage,
)
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.tasks import ScreenLease, TaskBoard
from models.fake import FakeStep
from tests.conftest import handshake, make_harness

LONG = "build the quarterly report"


class PerRequest:
    """Each request follows its own script, picked by its words."""

    name = "fake"

    def __init__(self, scripts: dict[str, list[FakeStep]]) -> None:
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.requests: list[ModelRequest] = []
        self._calls = 0

    async def status(self) -> ProviderStatus:
        return ProviderStatus(provider="fake", ready=True, detail="")

    def asked(self, request: ModelRequest) -> str:
        user = (m.text for m in reversed(request.messages) if m.role is Role.USER and not m.tool_results)
        return next(t for t in user if t)

    def of(self, words: str) -> list[ModelRequest]:
        return [r for r in self.requests if words in self.asked(r)]

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        key = next(k for k in self.scripts if k in self.asked(request))
        step = self.scripts[key].pop(0) if self.scripts[key] else FakeStep(text="Done.")
        if step.text:
            yield TextDelta(text=step.text)
        for name, args in step.tool_calls:
            self._calls += 1
            yield ToolCallRequested(call_id=f"c{self._calls}", name=name, input=args)
        stop = "tool_use" if step.tool_calls else "end_turn"
        yield Completed(provider="fake", model="fake-model", stop_reason=stop, usage=Usage(input_tokens=1,
                        output_tokens=1), cost_usd=0.0)  # fmt: skip


class Work:
    """A step that takes as long as the test says (released with `go`)."""

    def __init__(self) -> None:
        schema = {"type": "object", "properties": {"value": {"type": "string"}}}
        self.info = ToolInfo(name="work", description="work on a part", input_schema=schema,
                             risk=RiskLevel.WRITE, capabilities=(), source="test")  # fmt: skip
        self.go = threading.Event()
        self.runs: list[str] = []

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.WRITE, summary=f"Work on part {args.get('value')}")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.runs.append(str(args.get("value")))
        while not self.go.is_set():  # noqa: ASYNC110 - set from the test's thread
            await asyncio.sleep(0.01)
        return ToolResult(ok=True, output=f"part {args.get('value')} done")


LONG_SCRIPT = [
    FakeStep(tool_calls=[("work", {"value": "1"})]),
    FakeStep(tool_calls=[("work", {"value": "2"})]),
    FakeStep(text="The report is built."),
]


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Callable[[dict[str, list[FakeStep]]], tuple[Any, PerRequest, Work]]]:
    gens: list[Iterator[Any]] = []

    def build(scripts: dict[str, list[FakeStep]]) -> tuple[Any, PerRequest, Work]:
        provider, work = PerRequest({LONG: LONG_SCRIPT, **scripts}), Work()
        gen = make_harness(tmp_path, provider, extra_tools=(work,))  # type: ignore[arg-type]
        gens.append(gen)
        return next(gen), provider, work

    yield build
    for gen in gens:
        next(gen, None)


def send(ws: Any, request_id: str, text: str, session_id: str | None = None) -> None:
    ws.send_text(
        json.dumps({"type": "chat.send", "request_id": request_id, "text": text, "session_id": session_id})
    )


def until(ws: Any, done: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    events = []
    while True:
        events.append(json.loads(ws.receive_text()))
        if done(events[-1]):
            return events


def ended(request_id: str) -> Callable[[dict[str, Any]], bool]:
    return lambda e: (
        e["type"] in ("chat.done", "chat.error", "chat.cancelled") and e.get("request_id") == request_id
    )


def wait(condition: Callable[[], bool], seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def started_long(ws: Any) -> str:
    """Start the long request; returns its chat once its first step is running."""
    send(ws, "r1", LONG)
    events = until(ws, lambda e: e["type"] == "tool.started" and e["request_id"] == "r1")
    return next(e["session_id"] for e in events if e["type"] == "chat.started")


def test_a_new_message_is_answered_while_a_long_request_works(rig: Any) -> None:
    harness, provider, work = rig(
        {"how is it going": [FakeStep(tool_calls=[("tasks", {})]), FakeStep(text="It's on part 1.")]}
    )
    with harness.ws() as ws:
        handshake(ws)
        session = started_long(ws)
        send(ws, "r2", "how is it going", session)  # same chat, as when spoken
        events = until(ws, ended("r2"))
        assert events[-1]["type"] == "chat.done" and work.runs == [
            "1"
        ]  # answered; the report still on part 1
        asked = provider.of("how is it going")
        assert "t1" in asked[0].context and LONG in asked[0].context and "Work on part 1" in asked[0].context
        looked = asked[1].messages[-1].tool_results[0].output
        assert "t1" in looked and "working" in looked and "Work on part 1" in looked
        work.go.set()
        events = until(ws, ended("r1"))
        assert events[-1]["type"] == "chat.done" and work.runs == ["1", "2"]


def test_what_the_user_adds_reaches_the_running_request_before_its_next_step(rig: Any) -> None:
    add = {"task": "t1", "action": "add", "message": "also add a totals row"}
    harness, provider, work = rig(
        {"add a totals": [FakeStep(tool_calls=[("manage_task", add)]), FakeStep(text="OK.")]}
    )
    with harness.ws() as ws:
        handshake(ws)
        session = started_long(ws)
        send(ws, "r2", "also add a totals row", session)
        until(ws, ended("r2"))
        work.go.set()
        until(ws, ended("r1"))
    second = provider.of(LONG)[1].messages[-1]  # the report's next step: its tool result, then the words
    assert (
        second.tool_results
        and "also add a totals row" in second.text
        and "while you were working" in second.text
    )


def test_pause_holds_it_between_steps_and_resume_carries_on(rig: Any) -> None:
    harness, provider, work = rig(
        {
            "pause the report": [
                FakeStep(tool_calls=[("manage_task", {"task": "the report", "action": "pause"})])
            ],
            "carry on with": [FakeStep(tool_calls=[("manage_task", {"task": "t1", "action": "resume"})])],
        }
    )
    with harness.ws() as ws:
        handshake(ws)
        session = started_long(ws)
        send(ws, "r2", "pause the report", session)
        until(ws, ended("r2"))
        work.go.set()  # its step finishes; then it waits
        time.sleep(0.4)
        assert len(provider.of(LONG)) == 1 and work.runs == ["1"]
        send(ws, "r3", "carry on with the report", session)
        events = until(ws, ended("r1"))
        assert events[-1]["type"] == "chat.done" and work.runs == ["1", "2"]


def test_cancel_stops_only_that_request(rig: Any) -> None:
    harness, _, work = rig(
        {"cancel the": [FakeStep(tool_calls=[("manage_task", {"task": "t1", "action": "cancel"})])]}
    )
    with harness.ws() as ws:
        handshake(ws)
        session = started_long(ws)
        send(ws, "r2", "cancel the report", session)
        events = until(ws, lambda e: ended("r1")(e) or ended("r2")(e))
        events += until(ws, ended("r2") if ended("r1")(events[-1]) else ended("r1"))
    assert {e["request_id"]: e["type"] for e in events if ended(e.get("request_id", ""))(e)} == {
        "r1": "chat.cancelled",
        "r2": "chat.done",
    }
    assert work.runs == ["1"]


def test_the_screen_is_used_by_one_request_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ScreenLease, "HOLD_S", 0.2)

    async def go() -> None:
        board = TaskBoard()
        order: list[str] = []

        async def request(name: str, steps: int, pause_s: float = 0.0) -> None:
            board.start(name, name, name)
            for i in range(steps):
                async with board.use_screen(name):
                    order.append(f"{name}{i}")
                    await asyncio.sleep(0.05)
                await asyncio.sleep(pause_s)

        a = asyncio.create_task(request("a", 3, pause_s=0.02))
        await asyncio.sleep(0.01)
        b = asyncio.create_task(request("b", 2))
        await asyncio.sleep(0.03)
        assert board.get("b").state == "waiting_screen" and board.get("b").waiting_for == "t1"  # type: ignore[union-attr]
        await asyncio.gather(a, b)
        assert order == ["a0", "a1", "a2", "b0", "b1"]  # a's run of steps isn't broken into

        board.start("c", "c", "c")  # held, then left alone (thinking, or a long command elsewhere)
        async with board.use_screen("c"):
            pass
        t0 = time.monotonic()
        board.start("d", "d", "d")
        async with board.use_screen("d"):
            assert 0.15 <= time.monotonic() - t0 < 1.0  # once c has left it alone for a while

    asyncio.run(go())


def test_finding_a_task_by_its_words_and_what_the_model_is_told() -> None:
    async def go() -> None:
        board = TaskBoard()
        bill = board.start("r1", "x1", "pay my EB bill please")
        board.start("r2", "x2", "what's the weather")
        assert board.find("the EB bill") is bill and board.find("t1") is bill and board.find("1") is bill
        assert board.find("nothing like it") is None
        summary = board.summary(but="r2")
        assert "t1" in summary and "pay my EB bill" in summary and "manage_task" in summary
        await board.end(bill)
        assert board.summary(but="r2") == ""  # nothing else running: nothing added to the context

    asyncio.run(go())
