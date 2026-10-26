"""Cheap first: a turn moves to Balanced once, when a step fails or the task is multi-step."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.common import RiskLevel
from contracts.model import ModelTier
from contracts.tools import ToolContext, ToolResult
from core.config import Settings
from models.fake import FakeProvider, FakeStep
from tests.conftest import handshake, make_harness
from tests.tools_fixture import FakeTool


class Failing(FakeTool):
    def __init__(self) -> None:
        super().__init__("flaky", RiskLevel.READ, ())

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.runs.append(args)
        return ToolResult(ok=False, output="the app didn't respond")


@pytest.fixture
def run(tmp_path: Path) -> Iterator[Any]:
    gens = []

    def go(
        script: list[FakeStep], tier: str = "cheap", settings: Settings | None = None
    ) -> tuple[FakeProvider, dict]:
        provider = FakeProvider(script=script)
        tools = (FakeTool("peek", RiskLevel.READ, ()), Failing())
        gen = make_harness(tmp_path, provider, extra_tools=tools, settings=settings)
        gens.append(gen)
        harness = next(gen)
        with harness.ws() as ws:
            handshake(ws)
            ws.send_text(json.dumps({"type": "chat.send", "request_id": "r", "text": "do it", "tier": tier}))
            while (event := json.loads(ws.receive_text()))["type"] != "chat.done":
                pass
        return provider, event

    yield go
    for gen in gens:
        next(gen, None)


def tiers(provider: FakeProvider) -> list[ModelTier]:
    return [r.tier for r in provider.requests]


def test_a_failed_step_moves_the_rest_of_the_turn_up_once(run: Any) -> None:
    provider, done = run(
        [
            FakeStep(tool_calls=[("flaky", {"value": "1"})]),
            FakeStep(tool_calls=[("flaky", {"value": "2"})]),
            FakeStep(text="ok"),
        ]
    )
    assert tiers(provider) == [ModelTier.CHEAP, ModelTier.BALANCED, ModelTier.BALANCED]
    assert done["escalated"] == "a step failed"


def test_multi_step_work_escalates_after_three_rounds(run: Any) -> None:
    script = [FakeStep(tool_calls=[("peek", {"value": str(i)})]) for i in range(4)] + [FakeStep(text="done")]
    provider, done = run(script)
    assert tiers(provider) == [ModelTier.CHEAP] * 3 + [ModelTier.BALANCED] * 2
    assert done["escalated"] == "multi-step task"


def test_simple_asks_stay_cheap_and_chosen_tiers_are_respected(run: Any) -> None:
    provider, done = run([FakeStep(text="hi")])
    assert tiers(provider) == [ModelTier.CHEAP] and done["escalated"] is None
    provider, done = run([FakeStep(tool_calls=[("flaky", {"value": "1"})]), FakeStep(text="ok")], tier="max")
    assert tiers(provider) == [ModelTier.MAX, ModelTier.MAX] and done["escalated"] is None


def test_escalation_can_be_turned_off(run: Any) -> None:
    settings = Settings()
    settings = settings.model_copy(update={"agent": settings.agent.model_copy(update={"escalate": False})})
    provider, done = run(
        [FakeStep(tool_calls=[("flaky", {"value": "1"})]), FakeStep(text="ok")], settings=settings
    )
    assert tiers(provider) == [ModelTier.CHEAP, ModelTier.CHEAP] and done["escalated"] is None
