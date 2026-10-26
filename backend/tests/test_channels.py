"""Chat-app conversations through the channel service, with an in-memory channel."""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.channel import ApprovalDecision, ApprovalRequest, InboundMessage, OutboundMessage
from contracts.common import Capability, RiskLevel
from core.config import Settings, default_policies
from core.policy import decide
from core.runtime import Runtime
from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, make_harness
from tests.tools_fixture import FakeTool, make_tools

NAME = "telegram"  # a discovered plugin name, so the HTTP API accepts it


class FakeChannel:
    name = NAME

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[InboundMessage | ApprovalDecision] = asyncio.Queue()
        self.sent: list[str] = []
        self.asked: list[ApprovalRequest] = []
        self.closed: list[tuple[str, str]] = []

    async def receive(self) -> AsyncIterator[InboundMessage | ApprovalDecision]:
        while True:
            yield await self.inbox.get()

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message.text)

    async def request_approval(self, request: ApprovalRequest) -> None:
        self.asked.append(request)

    async def close_approval(self, approval_id: str, conversation_id: str, outcome: str) -> None:
        self.closed.append((approval_id, outcome))

    async def typing(self, conversation_id: str) -> None:
        pass


class Rig:
    def __init__(self, harness: Harness, tools: dict[str, FakeTool]) -> None:
        self.harness = harness
        self.tools = tools
        self.rt: Runtime = harness.client.app.state.runtime  # type: ignore[attr-defined]
        self.channel = FakeChannel()
        self.call(self._attach)

    async def _attach(self) -> None:
        self.rt.channels.attach(self.channel)

    def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        return self.harness.client.portal.call(fn, *args)  # type: ignore[union-attr]

    def say(self, text: str, user: str = "42", conversation: str = "c1") -> None:
        msg = InboundMessage(
            channel=NAME, conversation_id=conversation, user_id=user, user_name="ann", text=text
        )
        self.call(self.channel.inbox.put, msg)

    def answer(self, approval_id: str, approved: bool, user: str = "42") -> None:
        self.call(
            self.channel.inbox.put, ApprovalDecision(approval_id=approval_id, user_id=user, approved=approved)
        )

    def wait(self, check: Callable[[], bool], what: str) -> None:
        for _ in range(200):
            if check():
                return
            time.sleep(0.02)
        raise AssertionError(f"timed out waiting for {what}; sent={self.channel.sent}")

    def reply(self, n: int) -> str:
        self.wait(lambda: len(self.channel.sent) >= n, f"reply #{n}")
        return self.channel.sent[n - 1]

    def pair(self, user: str = "42") -> None:
        code = self.harness.client.post(f"/api/plugins/{NAME}/pair", headers=self.harness.auth).json()["code"]
        before = len(self.channel.sent)
        self.say(f"/pair {code}", user=user)
        assert self.reply(before + 1).startswith("Paired.")


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    stack = contextlib.ExitStack()

    def build(script: list[FakeStep] | None = None, settings: Settings | None = None) -> Rig:
        tools = make_tools()
        gen = make_harness(tmp_path, FakeProvider(script=script or []), extra_tools=tuple(tools.values()),
                           settings=settings)  # fmt: skip
        harness = next(gen)
        stack.callback(lambda: next(gen, None))
        return Rig(harness, tools)

    yield build
    stack.close()


def test_strangers_get_one_short_reply_and_nothing_runs(make: Callable[..., Rig]) -> None:
    rig = make()
    rig.say("delete my files")
    assert "private" in rig.reply(1)
    rig.say("hello?")
    time.sleep(0.2)
    assert len(rig.channel.sent) == 1 and rig.harness.provider.requests == []
    actions = [e["action"] for e in rig.harness.client.get("/api/audit", headers=rig.harness.auth).json()]
    assert "channel.unpaired_message" in actions


def test_pairing_needs_the_current_code_and_limits_tries(make: Callable[..., Rig]) -> None:
    rig = make()
    rig.say("/pair 123456")
    assert "no pairing code" in rig.reply(1)
    code = rig.harness.client.post(f"/api/plugins/{NAME}/pair", headers=rig.harness.auth).json()["code"]
    wrong = "000000" if code != "000000" else "111111"
    for i in range(5):
        rig.say(f"/pair {wrong}")
        rig.reply(2 + i)
    rig.say(f"/pair {code}")  # five wrong tries burned the code
    assert "no pairing code" in rig.reply(7)
    rig.pair()
    info = rig.harness.client.get("/api/plugins", headers=rig.harness.auth).json()
    telegram = next(p for p in info if p["manifest"]["name"] == NAME)
    assert [a["user_id"] for a in telegram["paired"]] == ["42"]


def test_paired_conversation_continues_its_own_chat(make: Callable[..., Rig]) -> None:
    rig = make([FakeStep(text="first answer"), FakeStep(text="second answer")])
    rig.pair()
    rig.say("hello")
    assert rig.reply(2) == "first answer"
    rig.say("and again")
    assert rig.reply(3) == "second answer"
    assert len(rig.harness.provider.requests[-1].messages) == 3  # history carried over
    rig.say("/new")
    assert rig.reply(4) == "Started a new chat."
    rig.say("fresh")
    rig.reply(5)
    assert len(rig.harness.provider.requests[-1].messages) == 1
    sessions = rig.harness.client.get("/api/sessions", headers=rig.harness.auth).json()
    assert len(sessions) == 2


def test_remote_changes_always_ask_and_only_the_asker_can_answer(make: Callable[..., Rig]) -> None:
    policies = {**default_policies(), Capability.FS_WRITE: "allow"}  # allowed locally without asking
    settings = Settings()
    settings = settings.model_copy(update={"tools": settings.tools.model_copy(update={"policies": policies})})
    rig = make([FakeStep(tool_calls=[("note", {"value": "x"})]), FakeStep(text="noted")], settings=settings)
    rig.pair()
    rig.pair(user="99")
    rig.say("make a note")
    rig.wait(lambda: bool(rig.channel.asked), "approval request")
    request = rig.channel.asked[0]
    assert request.tool == "note" and "chat apps always ask" in request.reason
    rig.answer(request.approval_id, True, user="99")  # someone else, even if paired, can't answer it
    time.sleep(0.2)
    assert rig.tools["note"].runs == []
    rig.answer(request.approval_id, True)
    assert rig.reply(len(rig.channel.sent) + 1) == "noted"
    assert rig.tools["note"].runs == [{"value": "x"}]
    assert rig.channel.closed == [(request.approval_id, "approved by you")]


def test_typed_commands_get_no_direct_pass_from_chat_apps(make: Callable[..., Rig]) -> None:
    rig = make()
    rig.pair()
    rig.say("remember that the spare key is under the mat")
    rig.wait(lambda: bool(rig.channel.asked), "approval request")
    assert rig.channel.asked[0].tool == "remember"
    rig.answer(rig.channel.asked[0].approval_id, False)
    assert "denied by you" in rig.reply(len(rig.channel.sent) + 1)
    assert rig.harness.client.get("/api/memories", headers=rig.harness.auth).json() == []


def test_one_turn_at_a_time_stop_and_kill_switch(make: Callable[..., Rig]) -> None:
    rig = make(
        [FakeStep(tool_calls=[("note", {"value": "x"})]), FakeStep(tool_calls=[("note", {"value": "y"})])]
    )
    rig.pair()
    rig.say("make a note")
    rig.wait(lambda: len(rig.channel.asked) == 1, "approval request")
    rig.say("another thing")
    assert "Still working" in rig.reply(2)
    rig.say("/stop")
    assert rig.reply(3) == "Stopped."
    rig.say("make another note")
    rig.wait(lambda: len(rig.channel.asked) == 2, "second approval")
    with rig.harness.ws() as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "control.stop_all"}))
        assert rig.reply(4) == "Stopped."
    assert rig.tools["note"].runs == []


def test_pause_and_unpair(make: Callable[..., Rig]) -> None:
    rig = make()
    rig.pair()
    rig.call(_set_paused, rig.rt, True)
    rig.say("hello")
    assert "Paused" in rig.reply(2)
    rig.call(_set_paused, rig.rt, False)
    response = rig.harness.client.delete(f"/api/plugins/{NAME}/paired/42", headers=rig.harness.auth)
    assert response.status_code == 204
    rig.say("hello")
    assert "private" in rig.reply(3)


async def _set_paused(rt: Runtime, paused: bool) -> None:
    rt.paused = paused


def test_remote_policy() -> None:
    def check(risk: RiskLevel, **kw: Any) -> tuple[str, bool]:
        allow = {Capability.FS_WRITE: "allow"}
        d = decide(tool="t", risk=risk, capabilities=(Capability.FS_WRITE,), policies=allow,  # type: ignore[arg-type]
                   tainted=False, session_grants={"t"}, **kw)  # fmt: skip
        return d.action, d.can_remember

    assert check(RiskLevel.WRITE, remote="telegram") == ("ask", False)
    assert check(RiskLevel.WRITE, remote="telegram", direct=True) == ("ask", False)
    assert check(RiskLevel.READ, remote="telegram")[0] == "allow"
    assert check(RiskLevel.WRITE)[0] == "allow"
