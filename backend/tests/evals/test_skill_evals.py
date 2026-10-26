"""Scenario evals for the skill learner: learn rate, replay reliability, model calls saved.

Each scenario runs a few model turns (scripted), then replays with fresh parameters and
checks every tool received exactly the expected arguments. Trap scenarios must never be
learned. Run with `pytest tests/evals -s` to see the report.
"""

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts.common import Capability, RiskLevel, Trust
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.config import Settings
from models.fake import FakeProvider, FakeStep
from tests.conftest import handshake, make_harness

LOCATOR = {"role": "push button", "name": "Save", "window": {"app": "editor"}}


class EvalTool:
    def __init__(
        self,
        name: str,
        *,
        trust: Trust = Trust.USER,
        verified: bool | None = None,
        target: dict[str, Any] | None = None,
        risk: RiskLevel = RiskLevel.WRITE,
    ) -> None:
        self.info = ToolInfo(
            name=name,
            description=name,
            input_schema={"type": "object"},
            risk=risk,
            capabilities=(Capability.INPUT_CONTROL,),
            source="eval",
        )
        self.runs: list[dict[str, Any]] = []
        self._trust, self._verified, self._target = trust, verified, target

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=self.info.risk, summary=self.info.name)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.runs.append(args)
        evidence: dict[str, Any] = {}
        if self._verified is not None:
            evidence["verified"] = self._verified
        if self._target:
            evidence["target"] = self._target
        return ToolResult(ok=True, output="ok", trust=self._trust, evidence=evidence)


@dataclass
class Scenario:
    name: str
    learn: list[tuple[str, list[tuple[str, dict[str, Any]]]]]
    """(request, tool calls the model makes) for each learning run."""
    replays: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = field(default_factory=list)
    """(request, expected tool calls) that must replay with no model call."""
    trap: bool = False
    """True when the learner must refuse to learn this."""


SCENARIOS = [
    Scenario(
        "web search on a site",
        learn=[
            (
                "show me wikipedia results for rust traits",
                [("browse", {"url": "https://en.wikipedia.org/w/index.php?search=rust+traits"})],
            ),
            (
                "show me wikipedia results for tides",
                [("browse", {"url": "https://en.wikipedia.org/w/index.php?search=tides"})],
            ),
        ],
        replays=[
            (
                "show me wikipedia results for black holes",
                [("browse", {"url": "https://en.wikipedia.org/w/index.php?search=black+holes"})],
            ),
            (
                "Show me wikipedia results for C++ templates",
                [("browse", {"url": "https://en.wikipedia.org/w/index.php?search=C%2B%2B+templates"})],
            ),
        ],
    ),
    Scenario(
        "open an app, then a page",
        learn=[
            (
                "start spotify and then open github.com",
                [("launch", {"app": "spotify"}), ("browse", {"url": "https://github.com"})],
            ),
            (
                "start firefox and then open example.org",
                [("launch", {"app": "firefox"}), ("browse", {"url": "https://example.org"})],
            ),
        ],
        replays=[
            (
                "start gimp and then open python.org",
                [("launch", {"app": "gimp"}), ("browse", {"url": "https://python.org"})],
            )
        ],
    ),
    Scenario(
        "write a note",
        learn=[
            (
                "jot buy milk into todo.txt",
                [("save_note", {"path": "~/notes/todo.txt", "content": "buy milk"})],
            ),
            (
                "jot call mom into family.txt",
                [("save_note", {"path": "~/notes/family.txt", "content": "call mom"})],
            ),
        ],
        replays=[
            (
                "jot book flights into travel.txt",
                [("save_note", {"path": "~/notes/travel.txt", "content": "book flights"})],
            )
        ],
    ),
    Scenario(
        "press a button in an app (locator, verified)",
        learn=[
            ("hit save in the editor", [("press", {"element_id": "e4"})]),
            ("hit save in the editor", [("press", {"element_id": "e9"})]),
        ],
        replays=[("hit save in the editor", [("press", {"element_id": LOCATOR})])],
    ),
    Scenario(
        "trap: depends on what was read",
        learn=[
            (
                "copy my notes into a new file",
                [("peek", {"path": "~/a"}), ("save_note", {"path": "~/b", "content": "x"})],
            )
        ]
        * 2,
        trap=True,
    ),
    Scenario(
        "trap: password",
        learn=[("type my password into the login box", [("type", {"text": "hunter2"})])] * 2,
        trap=True,
    ),
    Scenario(
        "trap: generated content",
        learn=[
            (
                "write a haiku to haiku.txt",
                [
                    (
                        "save_note",
                        {
                            "path": "~/haiku.txt",
                            "content": "An old silent pond, a frog jumps into the pond, splash! Silence again.",
                        },
                    )
                ],
            )
        ]
        * 2,
        trap=True,
    ),
]


def tools() -> dict[str, EvalTool]:
    made = [
        EvalTool("browse"),
        EvalTool("launch"),
        EvalTool("save_note"),
        EvalTool("press", verified=True, target={"element": LOCATOR}),
        EvalTool("peek", trust=Trust.UNTRUSTED, risk=RiskLevel.READ),
        EvalTool("type"),
    ]
    return {t.info.name: t for t in made}


def run_scenario(scenario: Scenario) -> dict[str, Any]:
    script: list[FakeStep] = []
    for _, calls in scenario.learn:
        script += [FakeStep(tool_calls=[c]) for c in calls] + [FakeStep(text="done")]
    script += [FakeStep(text="fallback")] * 10
    toolset = tools()
    provider = FakeProvider(script=script)
    with tempfile.TemporaryDirectory() as tmp:
        for harness in make_harness(
            Path(tmp), provider, extra_tools=tuple(toolset.values()), settings=Settings()
        ):
            with harness.ws() as ws:
                handshake(ws)
                for request, _ in scenario.learn:
                    send(ws, request)
                learned = harness.client.get("/api/skills", headers=harness.auth).json()
                model_calls = len(provider.requests)
                ok = 0
                for request, expected in scenario.replays:
                    before = {name: len(t.runs) for name, t in toolset.items()}
                    done = send(ws, request)
                    got = [(n, t.runs[i]) for n, t in toolset.items() for i in range(before[n], len(t.runs))]
                    ok += done["route"] == "skill" and sorted(map(json.dumps, got)) == sorted(
                        map(json.dumps, expected)
                    )
                saved_calls = len(provider.requests) == model_calls
    active = [s for s in learned if s["status"] == "active"]
    return {
        "scenario": scenario.name,
        "trap": scenario.trap,
        "learned": bool(active),
        "replays": len(scenario.replays),
        "replay_ok": ok,
        "zero_model_calls": saved_calls,
    }


def send(ws: Any, text: str) -> dict[str, Any]:
    ws.send_text(json.dumps({"type": "chat.send", "request_id": "r", "text": text}))
    while (event := json.loads(ws.receive_text()))["type"] not in {"chat.done", "chat.error"}:
        if event["type"] == "approval.request":
            ws.send_text(
                json.dumps(
                    {"type": "approval.respond", "approval_id": event["approval_id"], "approved": True}
                )
            )
    return dict(event)


def test_skill_learning_eval() -> None:
    results = [run_scenario(s) for s in SCENARIOS]
    real = [r for r in results if not r["trap"]]
    traps = [r for r in results if r["trap"]]
    replays = sum(r["replays"] for r in real)
    reliability = sum(r["replay_ok"] for r in real) / replays
    learn_rate = sum(r["learned"] for r in real) / len(real)
    false_learn = sum(r["learned"] for r in traps) / len(traps)
    print("\nskill eval")
    for r in results:
        print(f"  {r['scenario']:<45} learned={r['learned']!s:<5} replays {r['replay_ok']}/{r['replays']}")
    print(
        f"  learn rate {learn_rate:.0%} · replay reliability {reliability:.0%} · false-learn rate {false_learn:.0%}"
    )
    assert learn_rate == 1.0
    assert reliability == 1.0
    assert false_learn == 0.0
    assert all(r["zero_model_calls"] for r in real)
