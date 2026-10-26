"""Voice overlay: placement math, the controller (fake surface), the whole daemon path,
and the real Tk window on a private Xvfb display."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.overlay import OverlayView
from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatDelta,
    ChatDone,
    ChatStarted,
    DictationResult,
    ToolFinished,
    ToolStarted,
    Usage,
    VoiceFinal,
    VoicePartial,
)
from core.approvals import ApprovalBroker
from core.overlay import NEEDS_CHAT, OverlayController
from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, handshake, make_harness
from tests.tools_fixture import make_tools
from workers.common.displays import parse_xrandr
from workers.overlay.placement import Rect, monitor_at, place

BACKEND = Path(__file__).resolve().parents[1]

# ---- placement --------------------------------------------------------------------------

LEFT, RIGHT = Rect(-1920, 0, 1920, 1080), Rect(0, 0, 2560, 1440)


def test_the_overlay_opens_on_the_pointers_display() -> None:
    assert monitor_at(-500, 300, [LEFT, RIGHT]) == LEFT  # displays left of the main one have negative x
    assert monitor_at(2000, 1300, [LEFT, RIGHT]) == RIGHT
    assert monitor_at(-1000, 1150, [LEFT, RIGHT]) == LEFT  # pointer in a gap: the nearest display
    assert monitor_at(0, 0, []) is None
    assert place(RIGHT, 400, 200) == (24, 48)  # top-left, under the top bar
    assert place(LEFT, 400, 200) == (-1896, 48)
    assert place(Rect(0, 0, 300, 150), 400, 200) == (8, 0)  # never off a tiny screen's left edge


def test_xrandr_monitor_list() -> None:
    text = (
        "Monitors: 2\n"
        " 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1\n"
        " 1: +HDMI-1 2560/597x1440/336+1920-200  HDMI-1\n"
    )
    assert parse_xrandr(text) == [(0, 0, 1920, 1080, 1.0, True), (1920, -200, 2560, 1440, 1.0, False)]


# ---- controller --------------------------------------------------------------------------


class FakeSurface:
    def __init__(self) -> None:
        self.shown: list[OverlayView] = []
        self.hidden = 0

    async def show(self, view: OverlayView) -> None:
        self.shown.append(view)

    async def level(self, value: float) -> None:
        pass

    async def hide(self) -> None:
        self.hidden += 1


def controller(enabled: bool = True) -> tuple[OverlayController, FakeSurface, ApprovalBroker, list[str]]:
    broker, stops = ApprovalBroker(), []

    async def stop_all() -> None:
        stops.append("stop")

    ctl = OverlayController(broker, stop_all, lambda: enabled, hide_scale=0.01)
    surface = FakeSurface()
    ctl.surface = surface
    return ctl, surface, broker, stops


def done(request_id: str) -> ChatDone:
    return ChatDone(
        request_id=request_id,
        model="m",
        stop_reason="end_turn",
        usage=Usage(),
        cost_usd=0,
        latency_ms=1,
        first_token_ms=1,
        route="model",
        timings={},
    )


async def test_a_voice_request_from_listening_to_done_then_hides() -> None:
    ctl, surface, _, _ = controller()
    await ctl.hotkey("toggle_chat", listeners=1)
    await ctl.observe(VoicePartial(stream_id="s", text="what's the"))
    await ctl.observe(
        VoiceFinal(stream_id="s", text="what's the weather", mode="chat", release_to_final_ms=40)
    )
    ctl.track("r1", "what's the weather")
    await ctl.observe(ChatStarted(request_id="r1", trace_id="t", session_id="sess", tier="cheap"))
    await ctl.observe(
        ToolStarted(request_id="r1", call_id="c", tool="open_url", summary="Open weather.com", risk="read")
    )
    await ctl.observe(ToolFinished(request_id="r1", call_id="c", status="ok", duration_ms=5, preview=""))
    await ctl.observe(ChatDelta(request_id="r1", text="It's sunny,\nabout 24°C."))
    await ctl.observe(done("r1"))
    phases = [(v.phase, v.partial, v.status) for v in surface.shown]
    assert phases[:4] == [
        ("listening", "", "Listening…"),
        ("listening", "what's the", "Listening…"),
        ("thinking", "what's the weather", "Thinking…"),
        ("thinking", "what's the weather", "Thinking…"),
    ]
    assert phases[-2] == ("acting", "what's the weather", "It's sunny,")  # the answer arriving, one line
    assert phases[-1][0] == "done"
    # The box holds the conversation: what was said, each step (ticked off), the whole answer.
    running = next(v for v in surface.shown if v.phase == "acting")
    assert [(i.kind, i.text, i.done) for i in running.items] == [
        ("you", "what's the weather", True),
        ("step", "Open weather.com", False),
    ]
    assert [(i.kind, i.text, i.done) for i in surface.shown[-1].items] == [
        ("you", "what's the weather", True),
        ("step", "Open weather.com", True),
        ("answer", "It's sunny,\nabout 24°C.", True),
    ]
    await asyncio.sleep(0.1)
    assert surface.hidden == 0 and ctl.view is not None  # it stays up (HIDE_IDLE_S scaled: 1.2 s)


async def test_typed_turns_and_other_tabs_are_not_mirrored() -> None:
    ctl, surface, _, _ = controller()
    await ctl.observe(ChatStarted(request_id="typed", trace_id="t", session_id="s", tier="cheap"))
    await ctl.observe(ChatDelta(request_id="typed", text="hi"))
    assert surface.shown == []


async def test_inline_approval_is_answered_from_the_overlay() -> None:
    ctl, surface, broker, _ = controller()
    ctl.track("r1")
    waiting = asyncio.create_task(broker.wait("a1", can_remember=True, timeout_s=5))
    await asyncio.sleep(0)
    await ctl.observe(
        ApprovalRequested(
            approval_id="a1",
            request_id="r1",
            call_id="c",
            tool="note",
            risk="write",
            summary="Write ~/a.txt",
            reason="fs.write is set to ask",
            args={},
            untrusted_context=False,
            can_remember=True,
            expires_in_s=60,
        )
    )
    shown = surface.shown[-1]
    assert shown.approval is not None and shown.approval.summary == "Write ~/a.txt"
    await ctl.observe(ToolStarted(request_id="r1", call_id="c2", tool="x", summary="other", risk="read"))
    assert surface.shown[-1].approval is not None  # a pending question stays on screen
    assert not await ctl.decide("someone-else", True)
    assert await ctl.decide("a1", True)
    outcome = await waiting
    assert outcome.approved and not outcome.remember  # never "remember" from a glance at an overlay
    await ctl.observe(ApprovalResolved(approval_id="a1", approved=True, reason="approved by you"))
    assert surface.shown[-1].approval is None and surface.shown[-1].status == "approved by you"


async def test_no_chat_window_dictation_stop_and_switch() -> None:
    ctl, surface, _, stops = controller()
    await ctl.hotkey("toggle_chat", listeners=0)
    assert (surface.shown[-1].phase, surface.shown[-1].status) == ("needs_chat", NEEDS_CHAT)
    await ctl.observe(
        DictationResult(stream_id="s", text="Hello.", raw="hello", method="local", inserted=True, note="")
    )
    assert surface.shown[-1].status == "Typed into your app."
    await ctl.stop()
    assert stops == ["stop"] and surface.shown[-1].status == "Stopped."
    await ctl.dismiss()
    assert ctl.view is None
    off, off_surface, _, _ = controller(enabled=False)
    await off.observe(VoicePartial(stream_id="s", text="hi"))
    assert off_surface.shown == []


# ---- the daemon path: chat window + overlay on one session --------------------------------


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[tuple[Harness, FakeSurface, Any]]:
    tools = make_tools()
    script = [FakeStep(tool_calls=[("note", {"value": "milk"})]), FakeStep(text="Added milk to your list.")]
    gen = make_harness(tmp_path, FakeProvider(script=script), extra_tools=tuple(tools.values()))
    harness = next(gen)
    surface = FakeSurface()
    harness.client.app.state.runtime.overlay.surface = surface  # type: ignore[attr-defined]
    yield harness, surface, tools
    next(gen, None)


def test_voice_turn_shows_in_the_overlay_and_lands_in_chat_history(
    daemon: tuple[Harness, FakeSurface, Any],
) -> None:
    harness, surface, tools = daemon
    with harness.ws() as ws:
        handshake(ws)
        # the hotkey (a desktop shortcut) with the chat window open
        assert (
            harness.client.post(
                "/api/voice/command", headers=harness.auth, json={"action": "toggle_chat"}
            ).status_code
            == 204
        )
        assert json.loads(ws.receive_text()) == {"type": "voice.command", "action": "toggle_chat"}
        ws.send_text(
            json.dumps(
                {
                    "type": "chat.send",
                    "request_id": "v1",
                    "text": "add milk to my list",
                    "voice": {"stream_id": "s1", "release_to_final_ms": 40},
                }
            )
        )
        while (event := json.loads(ws.receive_text()))["type"] != "approval.request":
            pass
        assert surface.shown[-1].approval is not None  # the same question, in both surfaces
        # answered with the global "approve" shortcut, without touching either window
        assert (
            harness.client.post(
                "/api/voice/command", headers=harness.auth, json={"action": "approve"}
            ).status_code
            == 204
        )
        while (event := json.loads(ws.receive_text()))["type"] != "chat.done":
            pass
        session_id = None
    assert tools["note"].runs == [{"value": "milk"}]
    assert surface.shown[-1].phase == "done"
    assert surface.shown[-1].items[-1].text == "Added milk to your list."
    sessions = harness.client.get("/api/sessions", headers=harness.auth).json()
    session_id = sessions[0]["id"]
    texts = [
        m["text"]
        for m in harness.client.get(f"/api/sessions/{session_id}/messages", headers=harness.auth).json()
    ]
    assert texts[0] == "add milk to my list" and texts[-1] == "Added milk to your list."
    assert event["request_id"] == "v1"


def test_hotkey_without_a_chat_window_says_so(daemon: tuple[Harness, FakeSurface, Any]) -> None:
    harness, surface, _ = daemon
    harness.client.post("/api/voice/command", headers=harness.auth, json={"action": "toggle_chat"})
    assert surface.shown[-1].phase == "needs_chat"


async def test_a_late_target_doesnt_keep_the_overlay_open() -> None:
    ctl, surface, _, _ = controller()
    await ctl.hotkey("toggle_chat", listeners=0)  # "open the chat window", hides by itself
    await ctl.set_target("Screen 1 · Editor")  # the snapshot arrives afterwards
    await asyncio.sleep(0.1)
    assert surface.hidden == 1 and ctl.view is None
    await ctl.hotkey("toggle_chat", listeners=1)
    await ctl.set_target("Screen 2 · Chrome")
    assert surface.shown[-1].target == "Screen 2 · Chrome" and surface.shown[-1].phase == "listening"
