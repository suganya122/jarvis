import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.model import ModelError
from models.fake import FakeProvider
from tests.conftest import Harness, handshake, make_harness


def send(ws: Any, **payload: Any) -> None:
    ws.send_text(json.dumps(payload))


def recv_until(ws: Any, final_types: set[str]) -> list[dict[str, Any]]:
    events = []
    while True:
        event = json.loads(ws.receive_text())
        events.append(event)
        if event["type"] in final_types:
            return events


TERMINAL = {"chat.done", "chat.error", "chat.cancelled"}


def test_streams_reply_and_persists_history(harness: Harness) -> None:
    with harness.ws() as ws:
        handshake(ws)
        send(ws, type="chat.send", request_id="r1", text="hello there")
        events = recv_until(ws, TERMINAL)
        types = [e["type"] for e in events]
        assert types[0] == "chat.started"
        assert types[-1] == "chat.done"
        text = "".join(e["text"] for e in events if e["type"] == "chat.delta")
        assert text == "echo: hello there"
        session_id = events[0]["session_id"]

        send(ws, type="chat.send", request_id="r2", session_id=session_id, text="again")
        recv_until(ws, TERMINAL)

    provider = harness.provider
    assert isinstance(provider, FakeProvider)
    second = provider.requests[-1]
    assert [m.text for m in second.messages] == ["hello there", "echo: hello there", "again"]

    transcript = harness.client.get(f"/api/sessions/{session_id}/messages", headers=harness.auth).json()
    assert [m["role"] for m in transcript] == ["user", "assistant", "user", "assistant"]
    sessions = harness.client.get("/api/sessions", headers=harness.auth).json()
    assert sessions[0]["title"] == "hello there"


def test_done_reports_usage_and_latency(harness: Harness) -> None:
    with harness.ws() as ws:
        handshake(ws)
        send(ws, type="chat.send", request_id="r1", text="a b c")
        done = recv_until(ws, TERMINAL)[-1]
    assert done["usage"]["output_tokens"] == 4  # "echo:" + 3 words
    assert done["latency_ms"] >= 0
    assert done["first_token_ms"] is not None


@pytest.fixture
def slow_harness(tmp_path: Path) -> Iterator[Harness]:
    yield from make_harness(tmp_path, FakeProvider(reply=" ".join(["word"] * 200), delay_s=0.01))


def test_cancel_stops_stream_and_keeps_partial(slow_harness: Harness) -> None:
    with slow_harness.ws() as ws:
        handshake(ws)
        send(ws, type="chat.send", request_id="r1", text="long please")
        started = json.loads(ws.receive_text())
        assert json.loads(ws.receive_text())["type"] == "chat.delta"
        send(ws, type="chat.cancel", request_id="r1")
        events = recv_until(ws, TERMINAL)
    assert events[-1] == {"type": "chat.cancelled", "request_id": "r1"}
    transcript = slow_harness.client.get(
        f"/api/sessions/{started['session_id']}/messages", headers=slow_harness.auth
    ).json()
    assert transcript[-1]["role"] == "assistant"
    assert 0 < len(transcript[-1]["text"].split()) < 200


def test_duplicate_request_is_busy(slow_harness: Harness) -> None:
    with slow_harness.ws() as ws:
        handshake(ws)
        send(ws, type="chat.send", request_id="r1", text="one")
        send(ws, type="chat.send", request_id="r1", text="two")
        events = recv_until(ws, {"chat.error"})
        assert events[-1]["code"] == "busy"
        send(ws, type="chat.cancel", request_id="r1")
        recv_until(ws, {"chat.cancelled"})


def test_invalid_messages_are_rejected(harness: Harness) -> None:
    with harness.ws() as ws:
        handshake(ws)
        ws.send_text("not json")
        assert json.loads(ws.receive_text())["code"] == "invalid_message"
        send(ws, type="chat.send", request_id="r1", text="")
        assert json.loads(ws.receive_text())["code"] == "invalid_message"
        send(ws, type="chat.send", request_id="r1", text="x", extra="field")
        assert json.loads(ws.receive_text())["code"] == "invalid_message"
        send(ws, type="ping")
        assert json.loads(ws.receive_text()) == {"type": "pong"}


@pytest.fixture
def offline_harness(tmp_path: Path) -> Iterator[Harness]:
    error = ModelError(code="offline", message="Can't reach the Anthropic API.", retryable=True)
    yield from make_harness(tmp_path, FakeProvider(error=error))


def test_provider_error_is_reported(offline_harness: Harness) -> None:
    with offline_harness.ws() as ws:
        handshake(ws)
        send(ws, type="chat.send", request_id="r1", text="hi")
        last = recv_until(ws, TERMINAL)[-1]
    assert last["type"] == "chat.error"
    assert last["code"] == "offline"


def test_settings_roundtrip_and_validation(harness: Harness) -> None:
    settings = harness.client.get("/api/settings", headers=harness.auth).json()
    assert settings["default_tier"] == "cheap"
    settings["default_tier"] = "balanced"
    put = harness.client.put("/api/settings", headers=harness.auth, json=settings)
    assert put.status_code == 200
    assert harness.client.get("/api/settings", headers=harness.auth).json()["default_tier"] == "balanced"
    settings["provider"] = "nonsense"
    assert harness.client.put("/api/settings", headers=harness.auth, json=settings).status_code == 422


def test_api_key_goes_to_keychain_not_status(harness: Harness) -> None:
    key = "sk-ant-test-0123456789abcdefghij"
    assert (
        harness.client.put("/api/secrets/anthropic", headers=harness.auth, json={"api_key": key}).status_code
        == 204
    )
    status = harness.client.get("/api/status", headers=harness.auth)
    assert status.json()["api_key_source"] == "keychain"
    assert key not in status.text
    assert key not in harness.client.get("/api/settings", headers=harness.auth).text
    harness.client.delete("/api/secrets/anthropic", headers=harness.auth)
    assert harness.client.get("/api/status", headers=harness.auth).json()["api_key_source"] == "none"
