"""Instant commands end to end: zero model calls, same policy gate and audit."""

import contextlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment
from core.config import Settings
from core.latency import INSTANT_FIRST_ACTION_BUDGET_MS
from models.fake import FakeProvider
from tests.conftest import Harness, handshake, make_harness
from tests.tools_fixture import FakeTool

TERMINAL = {"chat.done", "chat.error", "chat.cancelled"}


class FakeApps(FakeTool):
    INSTALLED = ("Firefox", "Spotify")

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        name = str(args.get("name", ""))
        if name.lower() not in {a.lower() for a in self.INSTALLED}:
            return ToolAssessment(risk=self.info.risk, summary=f"Open {name}", error="no such app")
        return ToolAssessment(risk=self.info.risk, summary=f"Open {name.title()}")


def os_tools() -> dict[str, FakeTool]:
    tools = [
        FakeTool("open_url", RiskLevel.WRITE, (Capability.NET_FETCH,), output="Opened in the browser."),
        FakeApps("open_app", RiskLevel.WRITE, (Capability.INPUT_CONTROL,), output="Opened Firefox."),
        FakeTool("system_info", RiskLevel.READ, (), output="os: TestOS"),
        FakeTool(
            "media_control",
            RiskLevel.WRITE,
            (Capability.INPUT_CONTROL,),
            output="Skip to the next track: done.",
        ),
    ]
    return {t.info.name: t for t in tools}


Make = Callable[..., tuple[Harness, dict[str, FakeTool], FakeProvider]]


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    stack = contextlib.ExitStack()

    def build(settings: Settings | None = None) -> tuple[Harness, dict[str, FakeTool], FakeProvider]:
        tools = os_tools()
        provider = FakeProvider()
        gen = make_harness(tmp_path, provider, extra_tools=tuple(tools.values()), settings=settings)
        harness = next(gen)
        stack.callback(lambda: next(gen, None))
        return harness, tools, provider

    yield build
    stack.close()


def ask(ws: Any, text: str, **extra: Any) -> list[dict[str, Any]]:
    ws.send_text(json.dumps({"type": "chat.send", "request_id": "r1", "text": text, **extra}))
    events = []
    while (event := json.loads(ws.receive_text()))["type"] not in TERMINAL:
        events.append(event)
        if event["type"] == "approval.request":
            ws.send_text(
                json.dumps(
                    {"type": "approval.respond", "approval_id": event["approval_id"], "approved": True}
                )
            )
    return [*events, event]


def reply(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "chat.delta")


def test_search_runs_instantly_without_model_or_approval(make: Make) -> None:
    harness, tools, provider = make()
    with harness.ws() as ws:
        handshake(ws)
        events = ask(ws, "search for open source licenses")
    done = events[-1]
    assert done["route"] == "instant" and done["model"] == "instant"
    assert done["cost_usd"] == 0 and done["usage"]["input_tokens"] == 0
    assert "route_ms" in done["timings"] and "action_ms" in done["timings"]
    assert provider.requests == []  # zero LLM calls
    assert not [e for e in events if e["type"] == "approval.request"]
    assert tools["open_url"].runs[0]["url"].startswith("https://duckduckgo.com/?q=open+source+licenses")
    assert reply(events) == "Searching duckduckgo.com for “open source licenses”."


def test_open_known_app_and_fall_through_for_unknown(make: Make) -> None:
    harness, tools, provider = make()
    with harness.ws() as ws:
        handshake(ws)
        assert ask(ws, "open firefox")[-1]["route"] == "instant"
        assert ask(ws, "open blorpzap")[-1]["route"] == "model"
    assert len(tools["open_app"].runs) == 1
    assert len(provider.requests) == 1


def test_calculator_and_time_answers(make: Make) -> None:
    harness, _, provider = make()
    with harness.ws() as ws:
        handshake(ws)
        assert reply(ask(ws, "what is 15% of 80")) == "15% of 80 = 12"
        assert reply(ask(ws, "what time is it")).startswith("It's ")
    assert provider.requests == []


def test_force_model_and_disabled_router_go_to_the_model(make: Make) -> None:
    settings = Settings()
    settings.router.enabled = False
    harness, tools, provider = make(settings)
    with harness.ws() as ws:
        handshake(ws)
        assert ask(ws, "search for cats")[-1]["route"] == "model"
    assert tools["open_url"].runs == [] and len(provider.requests) == 1


def test_force_model_flag(make: Make) -> None:
    harness, _, provider = make()
    with harness.ws() as ws:
        handshake(ws)
        assert ask(ws, "2+2", force_model=True)[-1]["route"] == "model"
    assert len(provider.requests) == 1


def test_capability_turned_off_still_wins(make: Make) -> None:
    settings = Settings()
    settings.tools.policies = {**settings.tools.policies, "net.fetch": "deny"}  # type: ignore[dict-item]
    harness, tools, _ = make(settings)
    with harness.ws() as ws:
        handshake(ws)
        events = ask(ws, "search for cats")
    assert tools["open_url"].runs == []
    assert "turned off in Settings" in reply(events)


def test_asking_is_kept_when_direct_skip_is_off(make: Make) -> None:
    settings = Settings()
    settings.router.direct_without_asking = False
    harness, tools, _ = make(settings)
    with harness.ws() as ws:
        handshake(ws)
        events = ask(ws, "next track")
    assert [e for e in events if e["type"] == "approval.request"]
    assert len(tools["media_control"].runs) == 1


def test_instant_turn_is_audited_and_kept_in_history(make: Make) -> None:
    harness, _, _ = make()
    with harness.ws() as ws:
        handshake(ws)
        session = next(e for e in ask(ws, "open github.com") if e["type"] == "chat.started")["session_id"]
    entries = harness.client.get("/api/audit", headers=harness.auth).json()
    result = next(e for e in entries if e["action"] == "chat.result")
    assert result["detail"]["route"] == "instant" and result["detail"]["rule"] == "url"
    assert any(
        e["action"] == "tool.call" and e["detail"]["reason"] == "you asked for this directly" for e in entries
    )
    transcript = harness.client.get(f"/api/sessions/{session}/messages", headers=harness.auth).json()
    assert [m["text"] for m in transcript] == ["open github.com", "Opened github.com."]


def test_instant_latency_budget(make: Make) -> None:
    harness, _, _ = make()
    with harness.ws() as ws:
        handshake(ws)
        for _ in range(5):
            done = ask(ws, "open firefox")[-1]
            assert done["latency_ms"] < INSTANT_FIRST_ACTION_BUDGET_MS, done
