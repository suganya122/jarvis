"""Milestone 9: memory (phrases, tools, prompt context, API) and spend caps."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.memory import Memory
from core.config import Settings, SpendingSettings
from core.memory import HEADER, render, select, tidy
from core.router.rules import match
from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, handshake, make_harness

TERMINAL = {"chat.done", "chat.error", "chat.cancelled"}


def mem(text: str, at: float) -> Memory:
    return Memory(memory_id=str(at), text=text, source="user", created_at=at, updated_at=at)


# ---- pure ----------------------------------------------------------------------------


def test_everything_goes_in_when_it_fits_in_saved_order() -> None:
    memories = [mem("Sister is Priya", 1), mem("Prefers metric units", 2)]
    assert select(memories, "anything", 1_000) == memories
    assert render(memories) == f"{HEADER}\n- Sister is Priya\n- Prefers metric units"
    assert render([]) == ""


def test_over_budget_the_most_relevant_win_but_order_is_kept() -> None:
    memories = [
        mem("Dentist is Dr Rao on Main Street", 1),
        mem("Sister is Priya", 2),
        mem("Car is a blue Civic", 3),
    ]
    picked = select(memories, "what colour is my car?", 45)
    assert [m.text for m in picked] == ["Sister is Priya", "Car is a blue Civic"]  # saved order kept


def test_tidy() -> None:
    assert tidy("  my sister's name is   Priya. ") == "My sister's name is Priya"


@pytest.mark.parametrize(
    ("text", "tool", "args"),
    [
        ("remember that my sister's name is Priya", "remember", {"text": "my sister's name is Priya"}),
        ("please remember I prefer metric units", "remember", {"text": "I prefer metric units"}),
        ("forget about my sister", "forget", {"query": "my sister"}),
        ("what do you remember?", "recall_memories", {"query": ""}),
        ("what do you remember about my car", "recall_memories", {"query": "my car"}),
        ("remember to call mom", None, None),
        ("forget it", None, None),
    ],
)
def test_memory_phrases(text: str, tool: str | None, args: dict[str, Any] | None) -> None:
    from core.config import RouterSettings

    route = match(text, RouterSettings())
    assert (route.tool, route.args) == (tool, args) if route else tool is None


# ---- end to end ----------------------------------------------------------------------


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Any]:
    gens: list[Iterator[Harness]] = []

    def build(script: list[FakeStep] | None = None, settings: Settings | None = None) -> Harness:
        gen = make_harness(tmp_path, FakeProvider(script=script or []), settings=settings)
        gens.append(gen)
        return next(gen)

    yield build
    for gen in gens:
        next(gen, None)


def chat(ws: Any, text: str, approve: bool | None = None) -> list[dict[str, Any]]:
    ws.send_text(json.dumps({"type": "chat.send", "request_id": "r", "text": text}))
    events = []
    while True:
        event = json.loads(ws.receive_text())
        events.append(event)
        if event["type"] == "approval.request":
            assert approve is not None, "unexpected approval"
            ws.send_text(json.dumps({"type": "approval.respond", "approval_id": event["approval_id"],
                                     "approved": approve, "remember": False}))  # fmt: skip
        if event["type"] in TERMINAL:
            return events


def reply(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "chat.delta")


def test_typed_remember_is_instant_and_reaches_later_model_calls(make: Any) -> None:
    harness = make()
    with harness.ws() as ws:
        handshake(ws)
        events = chat(ws, "remember that my sister's name is Priya")
        assert reply(events) == "Got it. I'll remember: My sister's name is Priya"
        assert harness.provider.requests == []  # no model call
        chat(ws, "what's her name?")
    [memory] = harness.client.get("/api/memories", headers=harness.auth).json()
    assert memory["source"] == "user"
    request = harness.provider.requests[-1]
    assert request.context == f"{HEADER}\n- My sister's name is Priya"
    assert "Priya" not in request.system  # the cached system prompt stays byte-stable


def test_model_saved_memories_need_approval(make: Any) -> None:
    harness = make([FakeStep(tool_calls=[("remember", {"text": "user likes tea"})]), FakeStep(text="ok")])
    with harness.ws() as ws:
        handshake(ws)
        events = chat(ws, "i really like tea", approve=True)
    request = next(e for e in events if e["type"] == "approval.request")
    assert request["tool"] == "remember" and request["summary"] == "Remember: “User likes tea”"
    assert [m["source"] for m in harness.client.get("/api/memories", headers=harness.auth).json()] == [
        "assistant"
    ]


def test_forget_and_recall(make: Any) -> None:
    harness = make()
    with harness.ws() as ws:
        handshake(ws)
        chat(ws, "remember that my car is a blue Civic")
        chat(ws, "remember that my sister is Priya")
        assert reply(chat(ws, "what do you remember about my car")) == "- My car is a blue Civic"
        assert reply(chat(ws, "forget about my car")) == "Forgotten: My car is a blue Civic"
        assert reply(chat(ws, "forget about my car")) != ""  # no match now: falls through to the model
    texts = [m["text"] for m in harness.client.get("/api/memories", headers=harness.auth).json()]
    assert texts == ["My sister is Priya"]


def test_memory_api_and_switch(make: Any) -> None:
    settings = Settings()
    harness = make(
        settings=settings.model_copy(update={"memory": settings.memory.model_copy(update={"enabled": False})})
    )
    auth = harness.auth
    created = harness.client.post("/api/memories", headers=auth, json={"text": "works night shifts"})
    assert created.status_code == 201
    mid = created.json()["memory_id"]
    edited = harness.client.put(f"/api/memories/{mid}", headers=auth, json={"text": "works day shifts now"})
    assert edited.json()["text"] == "Works day shifts now"
    assert harness.client.post("/api/memories", headers=auth, json={"text": "x" * 501}).status_code == 422
    with harness.ws() as ws:
        handshake(ws)
        chat(ws, "hello")
    assert harness.provider.requests[-1].context == ""  # turned off: kept, not used
    assert harness.client.delete(f"/api/memories/{mid}", headers=auth).status_code == 204
    assert harness.client.delete(f"/api/memories/{mid}", headers=auth).status_code == 404
    actions = [e["action"] for e in harness.client.get("/api/audit", headers=auth).json()]
    assert {"memory.add", "memory.update", "memory.delete"} <= set(actions)


# ---- spend caps ------------------------------------------------------------------------


def capped(daily: float | None = 0.0, monthly: float | None = None) -> Settings:
    return Settings(spending=SpendingSettings(daily_cap_usd=daily, monthly_cap_usd=monthly))


def test_a_reached_cap_stops_model_calls_but_not_instant_commands(make: Any) -> None:
    harness = make(settings=capped(daily=0.0))
    cost = harness.client.get("/api/cost", headers=harness.auth).json()
    assert cost["capped"] == "daily" and cost["daily_cap_usd"] == 0.0 and cost["warning"]
    with harness.ws() as ws:
        handshake(ws)
        refused = chat(ws, "write me a poem")
        error = next(e for e in refused if e["type"] == "chat.error")
        assert error["code"] == "spend_cap" and "daily spending cap" in error["message"]
        assert reply(chat(ws, "what is 2+2")) == "2+2 = 4"  # instant: no model
    assert harness.provider.requests == []


def test_monthly_cap_wins_and_no_cap_means_none(make: Any) -> None:
    harness = make(settings=capped(daily=None, monthly=0.0))
    assert harness.client.get("/api/cost", headers=harness.auth).json()["capped"] == "monthly"
    harness.client.put("/api/settings", headers=harness.auth,
                       json=capped(daily=None, monthly=None).model_dump(mode="json"))  # fmt: skip
    cost = harness.client.get("/api/cost", headers=harness.auth).json()
    assert cost["capped"] is None and not cost["warning"]


def test_background_tasks_respect_the_cap(make: Any) -> None:
    harness = make(settings=capped(daily=0.0))
    with harness.ws() as ws:
        handshake(ws)
        ws.send_text(
            json.dumps({"type": "chat.send", "request_id": "b", "text": "research", "background": True})
        )
        while True:
            event = json.loads(ws.receive_text())
            if event["type"] == "task.update" and event["task"]["status"] in ("done", "failed"):
                break
    assert event["task"]["status"] == "failed" and "spending cap" in event["task"]["error"]
    assert harness.provider.requests == []


async def test_dictation_falls_back_to_local_cleanup_when_capped(tmp_path: Path) -> None:
    from core import db
    from core.dictation import DictationService
    from core.redact import Redactor
    from core.spend import CappedProvider, SpendGuard
    from core.store import Store

    conn = await db.connect(tmp_path / "t.sqlite3")
    store = Store(conn, Redactor())
    settings = capped(daily=0.0)
    inner = FakeProvider(reply="SHOULD NOT BE USED")
    provider = CappedProvider(lambda: inner, SpendGuard(store, lambda: settings))
    service = DictationService(lambda: settings, lambda: provider, store, None, None)  # type: ignore[arg-type]
    text, method, note = await service.clean("um so the meeting is at three", "t")
    assert method == "local" and "SHOULD NOT" not in text and inner.requests == []
    assert note
    await conn.close()
