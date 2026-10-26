"""Background tasks end to end: WebSocket and HTTP, approvals from any tab, queueing, restart."""

import asyncio
import contextlib
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.automation import TaskInfo
from contracts.common import RiskLevel
from core import db, paths
from core.automation_store import AutomationStore
from core.config import Settings
from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, handshake, make_harness
from tests.tools_fixture import FakeTool, make_tools

FINISHED = {"done", "failed", "cancelled"}
Make = Callable[..., tuple[Harness, dict[str, FakeTool], FakeProvider]]


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    stack = contextlib.ExitStack()

    def build(
        script: list[FakeStep] | None = None,
        settings: Settings | None = None,
        extra: tuple[FakeTool, ...] = (),
    ) -> tuple[Harness, dict[str, FakeTool], FakeProvider]:
        tools = make_tools() | {t.info.name: t for t in extra}
        provider = FakeProvider(script=script or [])
        gen = make_harness(tmp_path, provider, extra_tools=tuple(tools.values()), settings=settings)
        harness = next(gen)
        stack.callback(lambda: next(gen, None))
        return harness, tools, provider

    yield build
    stack.close()


def send_bg(ws: Any, text: str, request_id: str = "r1") -> None:
    ws.send_text(
        json.dumps({"type": "chat.send", "request_id": request_id, "text": text, "background": True})
    )


def until(ws: Any, stop: Callable[[dict[str, Any]], bool], on: Callable[[dict[str, Any]], None] | None = None
          ) -> list[dict[str, Any]]:  # fmt: skip
    events = []
    while True:
        event = json.loads(ws.receive_text())
        events.append(event)
        if on is not None:
            on(event)
        if stop(event):
            return events


def finished(event: dict[str, Any]) -> bool:
    return event["type"] == "task.update" and event["task"]["status"] in FINISHED


def statuses(events: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for e in events:
        if e["type"] == "task.update" and (not seen or seen[-1] != e["task"]["status"]):
            seen.append(e["task"]["status"])
    return seen


def test_background_turn_reports_live_status_and_notifies(make: Make) -> None:
    harness, tools, _ = make([FakeStep(text="Working.", tool_calls=[("peek", {"value": "a"})]),
                              FakeStep(text="All good.")])  # fmt: skip
    with harness.ws() as ws:
        handshake(ws)
        send_bg(ws, "check things")
        events = until(ws, lambda e: e["type"] == "notify")
    assert statuses(events) == ["queued", "running", "done"]
    done = [e for e in events if finished(e)][-1]["task"]
    assert done["origin"] == "user" and done["session_id"] and done["preview"].endswith("All good.")
    assert done["tokens"] > 0 and done["step"] == 1
    assert events[-1]["title"] == "Background task finished" and events[-1]["task_id"] == done["task_id"]
    assert tools["peek"].runs == [{"value": "a"}]
    listed = harness.client.get("/api/tasks", headers=harness.auth).json()
    assert [t["status"] for t in listed] == ["done"]


def test_approvals_reach_the_tab_and_the_queue_waits(make: Make) -> None:
    settings = Settings()
    settings = settings.model_copy(
        update={"background": settings.background.model_copy(update={"max_concurrent": 1})}
    )
    harness, tools, _ = make([FakeStep(tool_calls=[("note", {"value": "x"})]), FakeStep(text="noted"),
                              FakeStep(text="second")], settings=settings)  # fmt: skip
    with harness.ws() as ws:
        handshake(ws)
        send_bg(ws, "first", "r1")
        request = until(ws, lambda e: e["type"] == "approval.request")[-1]
        send_bg(ws, "second", "r2")
        queued = until(ws, lambda e: e["type"] == "task.update" and e["task"]["prompt"] == "second")[-1]
        assert queued["task"]["status"] == "queued"
        tasks = {
            t["prompt"]: t["status"] for t in harness.client.get("/api/tasks", headers=harness.auth).json()
        }
        assert tasks == {"first": "waiting_approval", "second": "queued"}
        ws.send_text(json.dumps({"type": "approval.respond", "approval_id": request["approval_id"],
                                 "approved": True, "remember": False}))  # fmt: skip
        done: set[str] = set()
        until(ws, lambda e: finished(e) and done.add(e["task"]["prompt"]) is None and len(done) == 2)  # type: ignore[func-returns-value]
    assert tools["note"].runs == [{"value": "x"}]


def test_background_tasks_never_take_over_the_screen(make: Make) -> None:
    open_app = FakeTool("open_app", RiskLevel.READ, ())
    harness, _, _ = make([FakeStep(tool_calls=[("open_app", {"value": "editor"})]), FakeStep(text="ok")],
                         extra=(open_app,))  # fmt: skip
    with harness.ws() as ws:
        handshake(ws)
        send_bg(ws, "open my editor")
        events = until(ws, finished)
    assert open_app.runs == []
    denied = [e for e in events if e["type"] == "tool.finished"]
    assert denied and denied[0]["status"] == "denied" and "take over your screen" in denied[0]["preview"]


def test_background_budget_is_its_own(make: Make) -> None:
    settings = Settings()
    settings = settings.model_copy(
        update={"background": settings.background.model_copy(update={"max_steps": 2})}
    )
    script = [FakeStep(tool_calls=[("peek", {"value": str(i)})]) for i in range(5)]
    harness, tools, _ = make(script, settings=settings)
    with harness.ws() as ws:
        handshake(ws)
        send_bg(ws, "loop")
        events = until(ws, finished)
    assert [e["stop_reason"] for e in events if e["type"] == "chat.done"] == ["step_limit"]
    assert len(tools["peek"].runs) == 2


def test_cancel_over_websocket_and_kill_switch(make: Make) -> None:
    harness, _, _ = make(
        [FakeStep(tool_calls=[("note", {"value": "x"})]), FakeStep(tool_calls=[("note", {})])]
    )
    with harness.ws() as ws:
        handshake(ws)
        send_bg(ws, "one", "r1")
        until(ws, lambda e: e["type"] == "approval.request")
        task_id = harness.client.get("/api/tasks", headers=harness.auth).json()[0]["task_id"]
        ws.send_text(json.dumps({"type": "task.control", "task_id": task_id, "action": "cancel"}))
        until(ws, finished)
        send_bg(ws, "two", "r2")
        until(ws, lambda e: e["type"] == "approval.request")
        ws.send_text(json.dumps({"type": "control.stop_all"}))
        until(ws, finished)
    statuses_ = [t["status"] for t in harness.client.get("/api/tasks", headers=harness.auth).json()]
    assert statuses_ == ["cancelled", "cancelled"]


def seed_interrupted(tmp_path: Path) -> str:
    async def seed() -> None:
        conn = await db.connect(paths.db_path(tmp_path))
        task = TaskInfo(task_id="crashed0crashed0", session_id=None, prompt="tidy downloads", origin="user",
                        status="running", created_at=time.time())  # fmt: skip
        await AutomationStore(conn).save_task(task)
        await conn.close()

    asyncio.run(seed())
    return "crashed0crashed0"


def test_interrupted_tasks_wait_for_the_user(tmp_path: Path, make: Make) -> None:
    task_id = seed_interrupted(tmp_path)
    harness, _, provider = make([FakeStep(text="resumed and done")])
    tasks = harness.client.get("/api/tasks", headers=harness.auth).json()
    assert [(t["task_id"], t["status"]) for t in tasks] == [(task_id, "interrupted")]
    assert provider.requests == []  # nothing ran on its own
    with harness.ws() as ws:
        handshake(ws)
        response = harness.client.post(f"/api/tasks/{task_id}/resume", headers=harness.auth)
        assert response.status_code == 204
        until(ws, finished)
    assert "Continue this task" in provider.requests[0].messages[-1].text
    assert harness.client.post(f"/api/tasks/{task_id}/resume", headers=harness.auth).status_code == 409


def test_discard_an_interrupted_task(tmp_path: Path, make: Make) -> None:
    task_id = seed_interrupted(tmp_path)
    harness, _, provider = make()
    assert harness.client.post(f"/api/tasks/{task_id}/discard", headers=harness.auth).status_code == 204
    assert harness.client.get("/api/tasks", headers=harness.auth).json()[0]["status"] == "cancelled"
    assert provider.requests == []


def test_internal_tools_are_hidden_and_blocked(make: Make) -> None:
    notify = FakeTool("desktop_notify", RiskLevel.READ, ())
    script = [FakeStep(tool_calls=[("desktop_notify", {"value": "spam"})]), FakeStep(text="k")]
    harness, _, provider = make(script, extra=(notify,))
    with harness.ws() as ws:
        handshake(ws)
        send_bg(ws, "notify me")
        until(ws, finished)
    assert notify.runs == []
    assert all(s.name != "desktop_notify" for s in provider.requests[0].tools)
