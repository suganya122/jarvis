"""Creating routines and watchers from chat, and managing them over HTTP."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from models.fake import FakeProvider, FakeStep
from tests.conftest import Harness, handshake, make_harness

TERMINAL = {"chat.done", "chat.error", "chat.cancelled"}


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    script = [
        FakeStep(tool_calls=[("create_routine", {"prompt": "water plants", "when": "every day at 7pm"})]),
        FakeStep(text="Set."),
    ]
    yield from make_harness(tmp_path, FakeProvider(script=script))


def chat(ws: Any, text: str, approve: bool | None = None) -> list[dict[str, Any]]:
    ws.send_text(json.dumps({"type": "chat.send", "request_id": "r", "text": text}))
    events = []
    while True:
        event = json.loads(ws.receive_text())
        events.append(event)
        if event["type"] == "approval.request":
            assert approve is not None, "unexpected approval"
            ws.send_text(json.dumps({"type": "approval.respond", "approval_id": event["approval_id"],
                                     "approved": approve, "remember": False}))  # fmt: skip
        if event["type"] in TERMINAL:
            return events


def get(harness: Harness, path: str) -> Any:
    response = harness.client.get(path, headers=harness.auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_typed_reminder_is_instant_and_needs_no_approval(harness: Harness) -> None:
    with harness.ws() as ws:
        handshake(ws)
        events = chat(ws, "remind me every weekday at 8am to stand up")
    done = next(e for e in events if e["type"] == "chat.done")
    assert done["route"] == "instant"
    text = "".join(e["text"] for e in events if e["type"] == "chat.delta")
    assert text.startswith("Saved. Every weekday at 08:00, I'll remind you.")
    [view] = get(harness, "/api/routines")
    assert view["routine"]["action"] == "notify" and view["summary"] == (
        "Every weekday at 08:00, I'll remind you: “stand up”"
    )
    assert harness.provider.requests == []  # zero model calls


def test_model_initiated_routines_need_approval(harness: Harness) -> None:
    with harness.ws() as ws:
        handshake(ws)
        events = chat(ws, "make sure i water the plants", approve=False)
    request = next(e for e in events if e["type"] == "approval.request")
    assert request["tool"] == "create_routine" and "Every day at 19:00" in request["summary"]
    assert get(harness, "/api/routines") == []


def test_manage_routines(harness: Harness) -> None:
    with harness.ws() as ws:
        handshake(ws)
        chat(ws, "every 2 hours, check my inbox")
        routine_id = get(harness, "/api/routines")[0]["routine"]["routine_id"]
        off = harness.client.patch(
            f"/api/routines/{routine_id}", headers=harness.auth, json={"enabled": False}
        )
        assert off.status_code == 200 and off.json()["routine"]["enabled"] is False
        on = harness.client.patch(f"/api/routines/{routine_id}", headers=harness.auth, json={"enabled": True})
        assert on.json()["routine"]["next_run_at"] > 0
        assert harness.client.post(f"/api/routines/{routine_id}/run", headers=harness.auth).status_code == 204
        task = json.loads(ws.receive_text())
        while task["type"] != "task.update":
            task = json.loads(ws.receive_text())
        assert (
            task["task"]["origin"] == f"routine:{routine_id}" and task["task"]["prompt"] == "check my inbox"
        )
    assert harness.client.delete(f"/api/routines/{routine_id}", headers=harness.auth).status_code == 204
    assert harness.client.delete(f"/api/routines/{routine_id}", headers=harness.auth).status_code == 404
    actions = [e["action"] for e in get(harness, "/api/audit?limit=200")]
    for action in ("routine.update", "routine.run", "routine.delete"):
        assert action in actions


def test_manage_watchers(harness: Harness) -> None:
    with harness.ws() as ws:
        handshake(ws)
        chat(ws, "tell me when example.com is back up")
    [watcher] = get(harness, "/api/watchers")
    assert watcher["target"] == "https://example.com" and watcher["status"] == "active"
    wid = watcher["watcher_id"]
    paused = harness.client.patch(f"/api/watchers/{wid}", headers=harness.auth, json={"status": "paused"})
    assert paused.json()["status"] == "paused"
    bad = harness.client.patch(f"/api/watchers/{wid}", headers=harness.auth, json={"status": "triggered"})
    assert bad.status_code == 422
    assert harness.client.delete(f"/api/watchers/{wid}", headers=harness.auth).status_code == 204
    assert get(harness, "/api/watchers") == []


def test_endpoints_need_the_token(harness: Harness) -> None:
    for path in ("/api/tasks", "/api/routines", "/api/watchers"):
        assert harness.client.get(path).status_code == 401
