"""Screenshots reach the model for the current turn only, are previewed locally, never stored."""

import contextlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.common import Capability, RiskLevel, Trust
from contracts.model import IMAGE_OMITTED, ChatMessage, ImageData, Role, ToolResultBlock
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from models.anthropic_provider import to_params
from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, handshake, make_harness

IMG = ImageData(media_type="image/jpeg", data_b64="aW1hZ2U=", width=1280, height=800)
THUMB = ImageData(media_type="image/jpeg", data_b64="dGh1bWI=", width=480, height=300)
CAPTURE = {"capture_id": "s1", "width": 1280, "height": 800}


class FakeCapture:
    info = ToolInfo(
        name="capture_screen",
        description="d",
        input_schema={"type": "object"},
        risk=RiskLevel.READ,
        capabilities=(Capability.SCREEN_READ,),
        source="test",
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="Take a screenshot of your screen")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append(args)
        return ToolResult(
            ok=True,
            output="Screenshot s1: 1280x800 pixels.",
            trust=Trust.UNTRUSTED,
            images=(IMG,),
            preview=THUMB,
            evidence={"capture": CAPTURE},
        )


Make = Callable[[list[FakeStep]], tuple[Harness, FakeCapture, FakeProvider]]


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    stack = contextlib.ExitStack()

    def build(script: list[FakeStep]) -> tuple[Harness, FakeCapture, FakeProvider]:
        tool, provider = FakeCapture(), FakeProvider(script=script)
        gen = make_harness(tmp_path, provider, extra_tools=(tool,))
        harness = next(gen)
        stack.callback(lambda: next(gen, None))
        return harness, tool, provider

    yield build
    stack.close()


def run(ws: Any, **send: Any) -> list[dict[str, Any]]:
    ws.send_text(json.dumps({"type": "chat.send", "request_id": "r1", **send}))
    events = []
    while (event := json.loads(ws.receive_text()))["type"] not in {"chat.done", "chat.error"}:
        events.append(event)
        if event["type"] == "approval.request":
            ws.send_text(
                json.dumps(
                    {"type": "approval.respond", "approval_id": event["approval_id"], "approved": True}
                )
            )
    return [*events, event]


def test_tool_screenshot_reaches_model_and_ui_but_not_storage(make: Make) -> None:
    harness, _, provider = make([FakeStep(tool_calls=[("capture_screen", {})]), FakeStep(text="I see it.")])
    with harness.ws() as ws:
        handshake(ws)
        events = run(ws, text="what's on my screen?")
        session = next(e for e in events if e["type"] == "chat.started")["session_id"]
        finished = next(e for e in events if e["type"] == "tool.finished")
        assert finished["image"] == "data:image/jpeg;base64,dGh1bWI=" and finished["capture_id"] == "s1"
        assert provider.requests[1].messages[-1].tool_results[0].images == (IMG,)
        run(ws, text="and now?", session_id=session)
    stored = provider.requests[-1].messages  # the next turn reads history back from the database
    old_result = next(m for m in stored if m.tool_results).tool_results[0]
    assert old_result.images == () and IMAGE_OMITTED in old_result.output


def test_attached_screen_goes_with_the_message_and_skips_the_router(make: Make) -> None:
    harness, tool, provider = make([FakeStep(text="That's a settings page.")])
    with harness.ws() as ws:
        handshake(ws)
        events = run(ws, text="search for cats", attach="screen")
    assert events[-1]["route"] == "model"  # never handled by the instant router
    assert tool.calls == [{"target": "screen"}]
    assert not [e for e in events if e["type"] == "approval.request"]  # pressing the button is direct
    sent = provider.requests[0].messages[-1]
    assert sent.images == (IMG,) and "Attached screenshot s1, 1280x800" in sent.text


def test_anthropic_image_blocks() -> None:
    params = to_params(
        [
            ChatMessage(role=Role.USER, text="where?", images=(IMG,)),
            ChatMessage(
                role=Role.ASSISTANT,
                text="",
                provider_state={
                    "provider": "anthropic",
                    "content": [{"type": "tool_use", "id": "c1", "name": "t", "input": {}}],
                },
            ),
            ChatMessage(
                role=Role.USER,
                text="",
                tool_results=(ToolResultBlock(call_id="c1", output="shot", images=(IMG,)),),
            ),
        ]
    )
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "aW1hZ2U="}}
    assert params[0]["content"] == [image, {"type": "text", "text": "where?"}]
    assert params[2]["content"][0]["content"] == [{"type": "text", "text": "shot"}, image]
