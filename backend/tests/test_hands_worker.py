"""The real hands worker process against the private fixture desktop, and through the daemon."""

import json
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.worker import WorkerConfig
from core.config import Settings
from core.worker_host import WorkerHost
from models.fake import FakeProvider, FakeStep
from tests.a11y_session import a11y_session, missing_requirements
from tests.conftest import handshake, make_harness

REASON = missing_requirements() if sys.platform.startswith("linux") else "Linux only"
pytestmark = pytest.mark.skipif(bool(REASON), reason=REASON or "")
CONFIG = WorkerConfig(
    allowed_roots=(),
    denied_paths=(),
    shell_allowlist=(),
    max_output_chars=4000,
    command_timeout_s=5,
    blocked_apps=("keepass",),
)


@pytest.fixture(scope="module")
def desktop() -> Iterator[dict[str, str]]:
    with a11y_session() as env:
        yield env


@pytest.fixture
async def host(desktop: dict[str, str]) -> AsyncIterator[WorkerHost]:
    worker = WorkerHost("hands", "workers.hands", extra_env=desktop)
    await worker.start(CONFIG)
    yield worker
    await worker.stop()


def short_ids(listing: str) -> dict[str, str]:
    return {line.split("'")[1]: line.split("  ", 1)[0] for line in listing.splitlines() if "'" in line}


async def test_worker_drives_the_fixture(host: WorkerHost) -> None:
    assert {t.name for t in host.tools} == {
        "list_windows",
        "find_elements",
        "click_element",
        "type_text",
        "press_keys",
        "scroll",
        "focus_window",
        "choose_menu",
        "read_element",
        "accessibility_status",
        "capture_screen",
        "highlight_area",
        "insert_text",
        "active_context",
    }
    snap = (await host.invoke("active_context", {})).evidence["context"]  # the real AT-SPI snapshot
    assert snap["display"]["name"] == "Screen 1" and snap["display"]["width"] == 1280
    assert [w["title"] for w in snap["windows"]] == ["Hands Fixture"]  # listed, with a short id
    assert snap["windows"][0]["window_id"].startswith("w")
    # this private desktop has no window manager, so nothing is "focused": it says so
    assert snap["window"] is None and "no focused window was found" in snap["limits"]
    assert snap["clipboard"] is None  # never read unless the user turned it on
    windows = await host.invoke("list_windows", {})
    assert "Hands Fixture" in windows.output and windows.trust == "untrusted"
    window_id = next(line.split()[0] for line in windows.output.splitlines() if "Hands Fixture" in line)
    found = await host.invoke("find_elements", {"window_id": window_id})
    ids = short_ids(found.output)
    assessed = await host.assess("click_element", {"element_id": ids["Delete all"]})
    assert assessed.risk == "destructive"
    clicked = await host.invoke("click_element", {"element_id": ids["Increment"]})
    assert clicked.ok and "Count: 1" in clicked.output
    typed = await host.invoke("type_text", {"element_id": ids["Name"], "text": "hello"})
    assert typed.ok
    read = await host.invoke("read_element", {"element_id": ids["Name"]})
    assert read.output == "hello"
    assert (await host.assess("type_text", {"element_id": ids["Password"], "text": "x"})).error


def test_full_stack_daemon_with_hands(desktop: dict[str, str], tmp_path: Path) -> None:
    """Model asks to list windows -> approval (screen.read asks) -> worker -> private desktop."""
    provider = FakeProvider(
        script=[FakeStep(tool_calls=[("list_windows", {})]), FakeStep(text="Here are your windows.")]
    )
    data = tmp_path / "data"
    data.mkdir()
    for harness in make_harness(data, provider, start_workers=True, settings=Settings(), worker_env=desktop):
        status = harness.client.get("/api/status", headers=harness.auth).json()
        assert status["hands"] is True
        with harness.ws() as ws:
            handshake(ws)
            ws.send_text(json.dumps({"type": "chat.send", "request_id": "r1", "text": "what's open?"}))
            approvals: list[dict[str, Any]] = []
            while (event := json.loads(ws.receive_text()))["type"] != "chat.done":
                if event["type"] == "approval.request":
                    approvals.append(event)
                    ws.send_text(
                        json.dumps(
                            {
                                "type": "approval.respond",
                                "approval_id": event["approval_id"],
                                "approved": True,
                            }
                        )
                    )
        assert approvals and approvals[0]["tool"] == "list_windows"
        result = provider.requests[-1].messages[-1].tool_results[0]
        assert "Hands Fixture" in result.output and result.output.startswith("<untrusted_content>")


async def test_accessibility_status(host: WorkerHost) -> None:
    status = await host.invoke("accessibility_status", {})
    assert status.ok and status.evidence["accessibility_bus"] is True
    assert "windows visible" in status.output


def test_learned_hands_skill_replays_by_locator(desktop: dict[str, str], tmp_path: Path) -> None:
    """Two model turns click 'Increment' via session ids; the third request replays the skill with
    no model call, finding the button again by locator, verified by the label change."""
    turn = [
        FakeStep(tool_calls=[("list_windows", {})]),
        FakeStep(tool_calls=[("find_elements", {"window_id": "w1"})]),
        FakeStep(tool_calls=[("click_element", {"element_id": "e1"})]),
        FakeStep(text="Clicked it."),
    ]
    provider = FakeProvider(script=turn + turn)
    data = tmp_path / "data"
    data.mkdir()
    for harness in make_harness(data, provider, start_workers=True, settings=Settings(), worker_env=desktop):
        with harness.ws() as ws:
            handshake(ws)
            notes = []
            for text in (
                "press increment in the fixture",
                "press increment in the fixture",
                "press increment in the fixture",
            ):
                ws.send_text(
                    json.dumps({"type": "chat.send", "request_id": "r", "text": text})
                )  # new chat each time
                while (event := json.loads(ws.receive_text()))["type"] != "chat.done":
                    if event["type"] == "approval.request":
                        ws.send_text(
                            json.dumps(
                                {
                                    "type": "approval.respond",
                                    "approval_id": event["approval_id"],
                                    "approved": True,
                                }
                            )
                        )
                notes.append((event["route"], (event.get("skill") or {}).get("event")))
        assert notes == [("model", "learned"), ("model", "promoted"), ("skill", "replayed")]
        assert len(provider.requests) == 8  # 4 model calls per learned turn, none for the replay
        skill = harness.client.get("/api/skills", headers=harness.auth).json()[0]
        step = skill["steps"][0]
        assert step["tool"] == "click_element" and step["checks"] == ["ok", "verified"]
        assert step["args_template"]["element_id"]["name"] == "Increment"
