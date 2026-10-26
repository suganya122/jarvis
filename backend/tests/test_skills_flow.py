"""Skills end to end: learn from model turns, promote, replay without the model, demote."""

import contextlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.config import Settings
from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, handshake, make_harness

SCHEMA = {"type": "object"}


class Recorder:
    """A write tool that fails when told to, so replays can break on purpose."""

    def __init__(self, name: str, risk: RiskLevel = RiskLevel.WRITE) -> None:
        self.info = ToolInfo(
            name=name,
            description=name,
            input_schema=SCHEMA,
            risk=risk,
            capabilities=(Capability.INPUT_CONTROL,),
            source="test",
        )
        self.runs: list[dict[str, Any]] = []
        self.fail = False

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=self.info.risk, summary=f"{self.info.name} {args}")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.runs.append(args)
        if self.fail:
            return ToolResult(ok=False, output="the app is gone")
        return ToolResult(ok=True, output="done")


Make = Callable[..., tuple[Harness, dict[str, Recorder], FakeProvider]]


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    stack = contextlib.ExitStack()

    def build(
        script: list[FakeStep], settings: Settings | None = None
    ) -> tuple[Harness, dict[str, Recorder], FakeProvider]:
        tools = {t.info.name: t for t in (Recorder("launch"), Recorder("shred", RiskLevel.DESTRUCTIVE))}
        provider = FakeProvider(script=script)
        gen = make_harness(tmp_path, provider, extra_tools=tuple(tools.values()), settings=settings)
        harness = next(gen)
        stack.callback(lambda: next(gen, None))
        return harness, tools, provider

    yield build
    stack.close()


def ask(ws: Any, text: str, approve: bool = True, **extra: Any) -> list[dict[str, Any]]:
    ws.send_text(json.dumps({"type": "chat.send", "request_id": "r1", "text": text, **extra}))
    events = []
    while (event := json.loads(ws.receive_text()))["type"] not in {"chat.done", "chat.error"}:
        events.append(event)
        if event["type"] == "approval.request":
            ws.send_text(
                json.dumps(
                    {"type": "approval.respond", "approval_id": event["approval_id"], "approved": approve}
                )
            )
    return [*events, event]


def model_turn(tool: str, args: dict[str, Any]) -> list[FakeStep]:
    return [FakeStep(tool_calls=[(tool, args)]), FakeStep(text="Done.")]


def test_learn_promote_and_replay_without_the_model(make: Make) -> None:
    script = model_turn("launch", {"app": "spotify"}) + model_turn("launch", {"app": "firefox"})
    harness, tools, provider = make(script)
    with harness.ws() as ws:
        handshake(ws)
        first = ask(ws, "fire up spotify please")[-1]
        assert first["route"] == "model" and first["skill"]["event"] == "learned"
        second = ask(ws, "fire up firefox")[-1]
        assert second["skill"]["event"] == "promoted"
        calls_before = len(provider.requests)
        replay = ask(ws, "Fire up Gimp")
    done = replay[-1]
    assert done["route"] == "skill" and done["model"] == "skill" and done["cost_usd"] == 0
    assert done["skill"]["event"] == "replayed"
    assert len(provider.requests) == calls_before  # zero model calls
    assert tools["launch"].runs[-1] == {"app": "Gimp"}
    assert not [e for e in replay if e["type"] == "approval.request"]  # like a direct command
    skills = harness.client.get("/api/skills", headers=harness.auth).json()
    assert skills[0]["triggers"] == ["fire up {app}"] and skills[0]["verified_successes"] == 3


def test_destructive_steps_always_re_ask_on_replay(make: Make) -> None:
    script = model_turn("shred", {"file": "old.log"}) + model_turn("shred", {"file": "tmp.log"})
    harness, tools, _provider = make(script)
    with harness.ws() as ws:
        handshake(ws)
        ask(ws, "shred old.log now")
        ask(ws, "shred tmp.log now")
        replay = ask(ws, "shred big.log now", approve=False)
    assert [e["tool"] for e in replay if e["type"] == "approval.request"] == ["shred"]
    assert replay[-1]["route"] == "skill" and replay[-1]["skill"]["event"] == "failed"
    assert tools["shred"].runs == [{"file": "old.log"}, {"file": "tmp.log"}]  # the denied replay never ran


def test_failing_replays_demote_the_skill(make: Make) -> None:
    script = model_turn("launch", {"app": "atom"}) + model_turn("launch", {"app": "brave"})
    harness, tools, _provider = make([*script, FakeStep(text="model fallback")])
    with harness.ws() as ws:
        handshake(ws)
        ask(ws, "fire up atom")
        ask(ws, "fire up brave")
        tools["launch"].fail = True
        assert ask(ws, "fire up gimp")[-1]["skill"]["event"] == "failed"
        assert ask(ws, "fire up kate")[-1]["skill"]["event"] == "demoted"
        after = ask(ws, "fire up vlc")[-1]
    assert after["route"] == "model"  # demoted skills no longer replay
    skill = harness.client.get("/api/skills", headers=harness.auth).json()[0]
    assert skill["status"] == "demoted" and skill["consecutive_failures"] == 2


def test_force_model_and_disabled_skills_go_to_the_model(make: Make) -> None:
    settings = Settings()
    settings.skills.promote_after = 1
    harness, _, _provider = make(
        [*model_turn("launch", {"app": "atom"}), FakeStep(text="x"), FakeStep(text="y")], settings
    )
    with harness.ws() as ws:
        handshake(ws)
        assert ask(ws, "fire up atom")[-1]["skill"]["event"] == "promoted"
        assert ask(ws, "fire up brave", force_model=True)[-1]["route"] == "model"
        sid = harness.client.get("/api/skills", headers=harness.auth).json()[0]["skill_id"]
        harness.client.patch(f"/api/skills/{sid}", headers=harness.auth, json={"enabled": False})
        assert ask(ws, "fire up gimp")[-1]["route"] == "model"


def test_skill_api_edits_are_validated(make: Make) -> None:
    settings = Settings()
    settings.skills.promote_after = 1
    harness, _, _ = make(model_turn("launch", {"app": "spotify"}), settings)
    with harness.ws() as ws:
        handshake(ws)
        ask(ws, "fire up spotify")
    auth = harness.auth
    sid = harness.client.get("/api/skills", headers=auth).json()[0]["skill_id"]
    bad = harness.client.patch(f"/api/skills/{sid}", headers=auth, json={"triggers": ["start {other}"]})
    assert bad.status_code == 422
    generic = harness.client.patch(f"/api/skills/{sid}", headers=auth, json={"triggers": ["{app}"]})
    assert generic.status_code == 422
    ok = harness.client.patch(
        f"/api/skills/{sid}",
        headers=auth,
        json={"name": "Launch an app", "triggers": ["fire up {app}", "start {app} for me"]},
    )
    assert ok.status_code == 200 and ok.json()["name"] == "Launch an app"
    assert harness.client.delete(f"/api/skills/{sid}", headers=auth).status_code == 204
    assert harness.client.get("/api/skills", headers=auth).json() == []
    actions = [e["action"] for e in harness.client.get("/api/audit", headers=auth).json()]
    assert {"skill.learned", "skill.edit", "skill.delete"} <= set(actions)
