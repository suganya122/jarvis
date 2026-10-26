"""Active context through the daemon: hotkey -> snapshot -> voice request with the target,
'this screen' only, a named other screen, and 'no, the other one'."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from contracts.common import Capability
from contracts.hands import Rect
from contracts.overlay import OverlayView
from contracts.worker import WorkerConfig
from core.config import Settings
from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, handshake, make_harness
from tests.local_tools import LocalTool
from workers.common.displays import name_displays
from workers.hands.capture import RawCapture
from workers.hands.context import ActiveContextTool
from workers.hands.env import HandsEnv
from workers.hands.fake import FakeHands, fixture_window
from workers.hands.screen_tools import CaptureScreen

DISPLAYS = name_displays([(0, 0, 1920, 1080, 1.0, True), (1920, 0, 2560, 1440, 1.0, False)])


class Surface:
    def __init__(self) -> None:
        self.shown: list[OverlayView] = []

    async def show(self, view: OverlayView) -> None:
        self.shown.append(view)

    async def level(self, value: float) -> None:
        pass

    async def hide(self) -> None:
        pass


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Any]:
    gens: list[Iterator[Harness]] = []

    def build(
        script: list[FakeStep], pointer: tuple[int, int] = (2500, 300)
    ) -> tuple[Harness, Surface, list[Any]]:
        mail = fixture_window("fake|mail", active=True)
        mail.app, mail.title, mail.bounds = (
            "Chrome",
            "Inbox - Gmail",
            Rect(x=2000, y=100, width=900, height=700),
        )
        mail.focus, mail.selection = next(iter(mail.nodes)), "Lunch at 1?"
        notes = fixture_window("fake|notes", active=False)
        notes.app, notes.title, notes.bounds = (
            "Editor",
            "notes.txt",
            Rect(x=100, y=100, width=800, height=600),
        )
        config = WorkerConfig(
            allowed_roots=(), denied_paths=(), shell_allowlist=(), max_output_chars=8000, command_timeout_s=5
        )
        grabs: list[Any] = []

        def grab(kind: str, bounds: Any) -> RawCapture:
            grabs.append(kind)
            return RawCapture(
                Image.new("RGB", (4480, 1440), "white"), "x11", desktop_width=4480, desktop_height=1440
            )

        env = HandsEnv(config, hands=FakeHands([mail, notes]), grab=grab)  # type: ignore[arg-type]
        env.displays, env.pointer, env.pointer_reliable = (
            (lambda: DISPLAYS),
            (lambda: pointer),
            (lambda: True),
        )
        tools = (LocalTool(ActiveContextTool(), env), LocalTool(CaptureScreen(), env))
        settings = Settings()
        policies = {**settings.tools.policies, Capability.SCREEN_READ: "allow"}  # no approval cards here
        settings = settings.model_copy(
            update={"tools": settings.tools.model_copy(update={"policies": policies})}
        )
        gen = make_harness(tmp_path, FakeProvider(script=script), extra_tools=tools, settings=settings)
        gens.append(gen)
        harness = next(gen)
        surface = Surface()
        harness.client.app.state.runtime.overlay.surface = surface  # type: ignore[attr-defined]
        return harness, surface, grabs

    yield build
    for gen in gens:
        next(gen, None)


def voice(harness: Harness, ws: Any, text: str, rid: str) -> list[dict[str, Any]]:
    assert (
        harness.client.post(
            "/api/voice/command", headers=harness.auth, json={"action": "toggle_chat"}
        ).status_code
        == 204
    )
    events = []
    ws.send_text(
        json.dumps(
            {
                "type": "chat.send",
                "request_id": rid,
                "text": text,
                "voice": {"stream_id": rid, "release_to_final_ms": 30},
            }
        )
    )
    while (event := json.loads(ws.receive_text()))["type"] not in ("chat.done", "chat.error"):
        events.append(event)
    return events


def test_this_screen_means_the_screen_under_the_pointer(daemon: Any) -> None:
    harness, surface, grabs = daemon(
        [
            FakeStep(tool_calls=[("capture_screen", {"target": "screen"})]),  # no display named: "this" one
            FakeStep(
                tool_calls=[("capture_screen", {"target": "screen", "display_id": "d1"})]
            ),  # not asked for
            FakeStep(text="Your inbox."),
        ]
    )
    with harness.ws() as ws:
        handshake(ws)
        events = voice(harness, ws, "read this screen", "v1")
    started = next(e for e in events if e["type"] == "chat.started")
    assert started["target"] == "Screen 2 · Chrome · Inbox - Gmail"
    assert any(
        v.target == "Screen 2 · Chrome · Inbox - Gmail" for v in surface.shown
    )  # shown in the overlay too
    finished = [(e["status"], e["preview"]) for e in events if e["type"] == "tool.finished"]
    assert finished[0][0] == "ok" and "Screen 2 (2560x1440 at 1920,0)" in finished[0][1]
    assert finished[1][0] == "blocked" and "I only capture another screen when you name it" in finished[1][1]
    assert grabs == ["screen"]  # the second screenshot never happened
    request = harness.provider.requests[0]
    assert "Active context" in request.context and 'display_id "d2"' in request.context
    assert "«Lunch at 1?»" in request.context and "Active context" not in request.system  # cache stays stable


def test_other_screens_when_named_and_correction(daemon: Any) -> None:
    harness, surface, _grabs = daemon(
        [
            FakeStep(tool_calls=[("capture_screen", {"target": "screen", "display_id": "d1"})]),
            FakeStep(text="Your notes."),
            FakeStep(text="Looking at Screen 1."),
        ]
    )
    with harness.ws() as ws:
        handshake(ws)
        events = voice(harness, ws, "what's on my other screen", "v1")
        assert [e["status"] for e in events if e["type"] == "tool.finished"] == ["ok"]  # named: allowed
        corrected = voice(harness, ws, "no, the other one", "v2")
    started = next(e for e in corrected if e["type"] == "chat.started")
    assert started["target"] == "Screen 1 · Editor · notes.txt"
    # the correction re-ran the last request on the new target
    assert harness.provider.requests[-1].messages[-1].text == "what's on my other screen"
    assert surface.shown[-1].target == "Screen 1 · Editor · notes.txt"


def test_typed_requests_have_no_context_and_selection_taints_the_chat(daemon: Any) -> None:
    harness, _, _ = daemon([FakeStep(text="hi"), FakeStep(text="ok")])
    with harness.ws() as ws:
        handshake(ws)
        ws.send_text(json.dumps({"type": "chat.send", "request_id": "t1", "text": "hello"}))
        while (event := json.loads(ws.receive_text()))["type"] != "chat.done":
            if event["type"] == "chat.started":
                assert event["target"] is None
        assert "Active context" not in harness.provider.requests[0].context
        voice(harness, ws, "summarize this", "v1")
    runtime = harness.client.app.state.runtime  # type: ignore[attr-defined]
    perms = list(runtime.chat._perms.values())[-1]
    assert perms.tainted  # text selected in another app is outside content
    audit = [e["action"] for e in harness.client.get("/api/audit", headers=harness.auth).json()]
    assert "context.snapshot" in audit
