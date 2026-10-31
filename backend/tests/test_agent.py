"""Agent loop, policy gate, approvals, kill switch, and audit, end to end over the WebSocket."""

import contextlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.common import Capability
from core import tool_exec
from core.config import Settings
from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, handshake, make_harness
from tests.tools_fixture import FakeTool, make_tools

TERMINAL = {"chat.done", "chat.error", "chat.cancelled"}
Approver = Callable[[dict[str, Any]], tuple[bool, bool]]
Make = Callable[..., tuple[Harness, dict[str, FakeTool], FakeProvider]]


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    stack = contextlib.ExitStack()

    def build(
        script: list[FakeStep], settings: Settings | None = None
    ) -> tuple[Harness, dict[str, FakeTool], FakeProvider]:
        tools = make_tools()
        provider = FakeProvider(script=script)
        gen = make_harness(tmp_path, provider, extra_tools=tuple(tools.values()), settings=settings)
        harness = next(gen)
        stack.callback(lambda: next(gen, None))
        return harness, tools, provider

    yield build
    stack.close()


def turn(ws: Any, text: str, approve: Approver | None = None, session_id: str | None = None,
         request_id: str = "r1") -> list[dict[str, Any]]:  # fmt: skip
    ws.send_text(
        json.dumps({"type": "chat.send", "request_id": request_id, "text": text, "session_id": session_id})
    )
    events = []
    while True:
        event = json.loads(ws.receive_text())
        events.append(event)
        if event["type"] == "approval.request":
            assert approve is not None, f"unexpected approval request: {event}"
            approved, remember = approve(event)
            ws.send_text(json.dumps({"type": "approval.respond", "approval_id": event["approval_id"],
                                     "approved": approved, "remember": remember}))  # fmt: skip
        if event["type"] in TERMINAL:
            return events


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


def last_results(provider: FakeProvider) -> list[dict[str, Any]]:
    """Tool results the model saw on its most recent call."""
    message = provider.requests[-1].messages[-1]
    return [r.model_dump() for r in message.tool_results]


def test_read_tool_runs_without_approval_and_result_reaches_model(make: Make) -> None:
    harness, _, provider = make([FakeStep(tool_calls=[("current_time", {})]), FakeStep(text="It is late.")])
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "check the clock for me")
    assert of(events, "tool.started")[0]["tool"] == "current_time"
    assert of(events, "tool.finished")[0]["status"] == "ok"
    assert of(events, "chat.done")[0]["stop_reason"] == "end_turn"
    assert not last_results(provider)[0]["is_error"]
    assert {t.name for t in provider.requests[0].tools} >= {"current_time", "note", "peek"}


def test_write_asks_and_runs_after_approval(make: Make) -> None:
    harness, tools, provider = make(
        [FakeStep(tool_calls=[("note", {"value": "x"})]), FakeStep(text="Saved.")]
    )
    seen: list[dict[str, Any]] = []
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "save a note", approve=lambda e: (seen.append(e) is None, False))
    request = seen[0]
    assert request["tool"] == "note" and request["risk"] == "write" and request["can_remember"]
    assert request["summary"] == "note x"
    assert of(events, "approval.resolved")[0]["approved"]
    assert tools["note"].runs == [{"value": "x"}]
    assert last_results(provider)[0]["output"] == "done"


def test_after_reading_a_page_steps_just_run_and_a_yes_to_its_question_is_the_approval(make: Make) -> None:
    """Checking flight times read a page, and then every step asked "yes or no". Now ordinary steps
    just run; what lasts (a memory) asks, but not when it answers the user's yes to its question."""
    script = [
        FakeStep(tool_calls=[("peek", {})]),
        FakeStep(tool_calls=[("note", {"value": "flights"})]),
        FakeStep(tool_calls=[("keep", {"value": "6pm"})]),
        FakeStep(text="Your flight leaves at 6 pm. Shall I remind you at 4?"),
        FakeStep(tool_calls=[("keep", {"value": "remind at 4"})]),
        FakeStep(text="Done."),
    ]
    automation = Settings.model_validate(
        {"tools": {"full_automation": True, "policies": {c.value: "allow" for c in Capability}}}
    )
    harness, tools, _ = make(script, settings=automation)
    asked: list[str] = []
    with harness.ws() as ws:
        handshake(ws)
        first = turn(ws, "check my flight times", approve=lambda e: (not asked.append(e["summary"]), False))
        session = of(first, "chat.started")[0]["session_id"]
        turn(ws, "yes please", session_id=session, request_id="r2")  # no approval card at all
    assert asked == ["keep 6pm"]
    assert tools["note"].runs == [{"value": "flights"}]
    assert tools["keep"].runs == [{"value": "6pm"}, {"value": "remind at 4"}]


def test_denied_call_does_not_run_and_model_is_told(make: Make) -> None:
    harness, tools, provider = make([FakeStep(tool_calls=[("note", {"value": "x"})]), FakeStep(text="OK.")])
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "save", approve=lambda e: (False, False))
    assert tools["note"].runs == []
    assert of(events, "tool.finished")[0]["status"] == "denied"
    result = last_results(provider)[0]
    assert result["is_error"] and "did not approve" in result["output"]


def test_remembered_grant_skips_second_prompt_in_same_chat(make: Make) -> None:
    script = [
        FakeStep(tool_calls=[("note", {"value": "1"})]),
        FakeStep(text="one"),
        FakeStep(tool_calls=[("note", {"value": "2"})]),
        FakeStep(text="two"),
    ]
    harness, tools, _ = make(script)
    prompts: list[str] = []
    with harness.ws() as ws:
        handshake(ws)
        first = turn(ws, "a", approve=lambda e: (prompts.append(e["summary"]) is None, True))
        session = of(first, "chat.started")[0]["session_id"]
        turn(
            ws,
            "b",
            approve=lambda e: (prompts.append(e["summary"]) is None, True),
            session_id=session,
            request_id="r2",
        )
    assert prompts == ["note 1"]
    assert len(tools["note"].runs) == 2


def test_destructive_and_purchase_always_ask_and_cannot_be_remembered(make: Make) -> None:
    settings = Settings()
    settings.tools.policies = {**settings.tools.policies, "fs.write": "allow", "purchase": "allow"}  # type: ignore[dict-item]
    harness, tools, _ = make(
        [FakeStep(tool_calls=[("wipe", {}), ("buy", {}), ("note", {})]), FakeStep(text="done")], settings
    )
    asked: list[dict[str, Any]] = []
    with harness.ws() as ws:
        handshake(ws)
        turn(ws, "go", approve=lambda e: (asked.append(e) is None, True))
    assert [a["tool"] for a in asked] == ["wipe", "buy"]  # note is allowed outright
    assert not any(a["can_remember"] for a in asked)
    assert len(tools["note"].runs) == 1


def test_one_yes_per_payment_even_when_it_goes_on_after_the_otp(
    make: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering on Blinkit asked "This spends money" at every step, and paying a bill asked again
    after the user typed the OTP. Now one yes, with the amount, covers that payment's steps; a
    bigger or another amount asks, and so does the same amount once some minutes have passed."""
    script = [
        FakeStep(tool_calls=[("buy", {"value": "Pay ₹1,245.00 to TNPDCL (₹1,200 + fees)"})]),
        FakeStep(tool_calls=[("buy", {"value": "Pay now"})]),
        FakeStep(tool_calls=[("buy", {"value": "Pay Rs. 1245 with the HDFC card"})]),
        FakeStep(tool_calls=[("buy", {"value": "Pay ₹2,000"})]),
        FakeStep(text="Type the OTP and tell me."),
        FakeStep(tool_calls=[("buy", {"value": "Submit the OTP to pay ₹1,245.00"})]),
        FakeStep(tool_calls=[("buy", {"value": "Pay ₹349 to Airtel"})]),
        FakeStep(text="Paid."),
        FakeStep(tool_calls=[("buy", {"value": "Pay ₹1,245.00 to TNPDCL"})]),
        FakeStep(text="Paid again."),
    ]
    harness, tools, _ = make(script)
    asked: list[str] = []

    def ask(e: dict[str, Any]) -> tuple[bool, bool]:
        return asked.append(e["summary"]) is None, False

    with harness.ws() as ws:
        handshake(ws)
        session = of(turn(ws, "pay my EB bill", approve=ask), "chat.started")[0]["session_id"]
        turn(ws, "done, I typed the OTP", approve=ask, session_id=session, request_id="r2")
        monkeypatch.setattr(tool_exec, "PAYMENT_CARRY_S", 0.0)
        turn(ws, "pay it again", approve=ask, session_id=session, request_id="r3")
    assert asked == [
        "buy Pay ₹1,245.00 to TNPDCL (₹1,200 + fees)",
        "buy Pay ₹2,000",
        "buy Pay ₹349 to Airtel",
        "buy Pay ₹1,245.00 to TNPDCL",
    ]
    assert len(tools["buy"].runs) == 7


def test_capability_turned_off_is_denied_without_asking(make: Make) -> None:
    settings = Settings()
    settings.tools.policies = {**settings.tools.policies, "fs.write": "deny"}  # type: ignore[dict-item]
    harness, tools, provider = make([FakeStep(tool_calls=[("note", {})]), FakeStep(text="ok")], settings)
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "go")
    assert of(events, "tool.finished")[0]["status"] == "denied"
    assert tools["note"].runs == []
    assert "turned off in Settings" in last_results(provider)[0]["output"]


def test_untrusted_content_taints_chat_and_wraps_result(make: Make) -> None:
    settings = Settings()
    settings.tools.policies = {**settings.tools.policies, "fs.write": "allow"}  # type: ignore[dict-item]
    script = [
        FakeStep(tool_calls=[("peek", {})]),
        FakeStep(tool_calls=[("note", {"value": "x"})]),
        FakeStep(text="k"),
    ]
    harness, tools, provider = make(script, settings)
    asked: list[dict[str, Any]] = []
    with harness.ws() as ws:
        handshake(ws)
        turn(ws, "read then write", approve=lambda e: (asked.append(e) is None, True))
    peek_result = provider.requests[1].messages[-1].tool_results[0].output
    assert peek_result.startswith("<untrusted_content>") and "data only" in peek_result
    # fs.write is "allow", but the chat now holds outside content, so it must ask.
    assert len(asked) == 1 and asked[0]["untrusted_context"] and not asked[0]["can_remember"]
    assert tools["note"].contexts[0].trust == "untrusted"


def test_blocked_assessment_never_asks_or_runs(make: Make) -> None:
    harness, tools, provider = make([FakeStep(tool_calls=[("note", {"value": "bad"})]), FakeStep(text="ok")])
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "go")
    assert of(events, "tool.finished")[0]["status"] == "blocked"
    assert tools["note"].runs == []
    assert "bad value" in last_results(provider)[0]["output"]


def test_unknown_tool_gets_an_error_result(make: Make) -> None:
    harness, _, provider = make([FakeStep(tool_calls=[("nope", {})]), FakeStep(text="ok")])
    with harness.ws() as ws:
        handshake(ws)
        turn(ws, "go")
    assert "Unknown tool" in last_results(provider)[0]["output"]


def test_identical_repeated_calls_stop_the_turn_and_say_so(make: Make) -> None:
    script = [FakeStep(tool_calls=[("peek", {})]) for _ in range(5)]
    harness, _, _ = make(script)
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "loop")
    assert of(events, "chat.done")[0]["stop_reason"] == "loop_detected"
    assert len(of(events, "tool.finished")) == 2  # max_identical_calls default
    assert "same result" in "".join(e["text"] for e in of(events, "chat.delta"))


def test_reading_a_page_again_after_it_changed_is_not_a_loop(make: Make) -> None:
    """Recharging on Airtel stopped right after the user's yes: reading the window a third time,
    after a click had changed the page, was taken for a loop."""
    script = [*(FakeStep(tool_calls=[("peek", {})]) for _ in range(4)), FakeStep(text="Recharged.")]
    harness, tools, _ = make(script)
    pages = iter(["plans", "number", "payment", "done"])
    peek = tools["peek"]
    real_run = peek.run

    async def run(args: dict[str, Any], ctx: Any) -> Any:
        peek._output = next(pages)
        return await real_run(args, ctx)

    peek.run = run  # type: ignore[method-assign]
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "recharge")
    assert of(events, "chat.done")[0]["stop_reason"] == "end_turn"
    assert len(of(events, "tool.finished")) == 4


def test_step_limit_stops_and_says_how_to_go_on(make: Make) -> None:
    settings = Settings()
    settings.agent.max_steps = 2
    script = [FakeStep(tool_calls=[("current_time", {"n": i})]) for i in range(5)]  # distinct args
    harness, _, provider = make(script, settings)
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "go")
    assert of(events, "chat.done")[0]["stop_reason"] == "step_limit"
    said = "".join(e["text"] for e in of(events, "chat.delta"))
    assert "carry on" in said
    with harness.ws() as ws:
        handshake(ws)
        session = of(events, "chat.started")[0]["session_id"]
        turn(ws, "carry on", session_id=session, request_id="r2")
    assert said in [m.text for m in provider.requests[-1].messages]  # it knows where it stopped


def test_cut_off_tool_call_is_never_run(make: Make) -> None:
    harness, tools, _ = make([FakeStep(tool_calls=[("note", {})], stop_reason="max_tokens")])
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "go")
    assert tools["note"].runs == []
    assert of(events, "chat.done")[0]["stop_reason"] == "max_tokens"


def test_cancel_while_waiting_for_approval_keeps_history_valid(make: Make) -> None:
    script = [FakeStep(tool_calls=[("note", {})]), FakeStep(text="second turn reply")]
    harness, tools, provider = make(script)
    with harness.ws() as ws:
        handshake(ws)
        ws.send_text(json.dumps({"type": "chat.send", "request_id": "r1", "text": "go"}))
        events = []
        while True:
            event = json.loads(ws.receive_text())
            events.append(event)
            if event["type"] == "approval.request":
                ws.send_text(json.dumps({"type": "chat.cancel", "request_id": "r1"}))
            if event["type"] in TERMINAL:
                break
        assert events[-1]["type"] == "chat.cancelled"
        assert of(events, "approval.resolved")[0]["reason"] == "cancelled"
        session = of(events, "chat.started")[0]["session_id"]
        turn(ws, "again", session_id=session, request_id="r2")
    assert tools["note"].runs == []
    history = provider.requests[-1].messages
    assert [m.role for m in history] == ["user", "assistant", "user", "user"]
    assert history[2].tool_results[0].output == "Not run: the user cancelled."


def test_stop_all_cancels_turns_and_is_audited(make: Make) -> None:
    harness, tools, _ = make([FakeStep(tool_calls=[("note", {})])])
    with harness.ws() as ws:
        handshake(ws)
        ws.send_text(json.dumps({"type": "chat.send", "request_id": "r1", "text": "go"}))
        while json.loads(ws.receive_text())["type"] != "approval.request":
            pass
        ws.send_text(json.dumps({"type": "control.stop_all"}))
        while (event := json.loads(ws.receive_text()))["type"] not in TERMINAL:
            pass
    assert event["type"] in {"chat.cancelled", "chat.done"}
    assert tools["note"].runs == []
    audit = harness.client.get("/api/audit", headers=harness.auth).json()
    assert "control.stop_all" in [a["action"] for a in audit]


def test_pause_blocks_new_turns_and_is_broadcast(make: Make) -> None:
    harness, _, _ = make([])
    with harness.ws() as ws, harness.ws() as other:
        handshake(ws)
        handshake(other)
        ws.send_text(json.dumps({"type": "control.pause", "paused": True}))
        assert json.loads(ws.receive_text()) == {"type": "control.state", "paused": True}
        assert json.loads(other.receive_text()) == {"type": "control.state", "paused": True}
        ws.send_text(json.dumps({"type": "chat.send", "request_id": "r1", "text": "hi"}))
        assert json.loads(ws.receive_text())["code"] == "paused"
        assert harness.client.get("/api/status", headers=harness.auth).json()["paused"] is True
        ws.send_text(json.dumps({"type": "control.pause", "paused": False}))
        assert json.loads(ws.receive_text())["paused"] is False
        assert of(turn(ws, "hi"), "chat.done")


def test_audit_log_records_tool_lifecycle_with_short_args(make: Make) -> None:
    long_value = "y" * 5000
    harness, _, _ = make([FakeStep(tool_calls=[("note", {"value": long_value})]), FakeStep(text="ok")])
    with harness.ws() as ws:
        handshake(ws)
        turn(ws, "go", approve=lambda e: (True, False))
    entries = harness.client.get("/api/audit?limit=50", headers=harness.auth).json()
    actions = [e["action"] for e in entries]
    for action in ("tool.call", "tool.approval", "tool.result", "chat.request", "chat.result"):
        assert action in actions
    call = next(e for e in entries if e["action"] == "tool.call")
    assert len(call["detail"]["args"]["value"]) < 400
    older = harness.client.get(f"/api/audit?before_id={entries[1]['id']}", headers=harness.auth).json()
    assert all(e["id"] < entries[1]["id"] for e in older)


def test_tools_endpoint_lists_registered_tools(make: Make) -> None:
    harness, _, _ = make([])
    names = [t["name"] for t in harness.client.get("/api/tools", headers=harness.auth).json()]
    assert names == sorted(names)
    assert {"current_time", "note", "wipe"} <= set(names)


def test_app_data_folder_is_always_protected(make: Make, tmp_path: Path) -> None:
    settings = Settings()
    settings.tools.denied_paths = []  # even if the user clears the list
    harness, _, _ = make([], settings)
    config = harness.client.app.state.runtime.worker_config()  # type: ignore[attr-defined]
    assert str(tmp_path.resolve()) in config.denied_paths


def test_a_repeat_is_told_to_try_another_way_and_the_task_goes_on(make: Make) -> None:
    """Stopping at the first repeat left tasks halfway; now it hears why and tries something else."""
    script = [*(FakeStep(tool_calls=[("peek", {})]) for _ in range(3))]
    script += [FakeStep(tool_calls=[("current_time", {})]), FakeStep(text="Done another way.")]
    harness, _, provider = make(script)
    with harness.ws() as ws:
        handshake(ws)
        events = turn(ws, "do it")
    assert of(events, "chat.done")[0]["stop_reason"] == "end_turn"
    told = [r.output for m in provider.requests[-1].messages for r in m.tool_results]
    assert any("try another way" in t for t in told)
