"""Speech-to-text, the voice worker, and voice through the daemon (real model, test speech)."""

import asyncio
import base64
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from contracts.voice import TranscriptEvent
from contracts.worker import WorkerConfig
from core.latency import INSTANT_FIRST_ACTION_BUDGET_MS, VOICE_RELEASE_TO_FINAL_BUDGET_MS
from core.worker_host import WorkerHost
from models.fake import FakeProvider
from tests import voice_audio
from tests.conftest import Harness, handshake, make_harness

pytestmark = pytest.mark.skipif(bool(voice_audio.missing()), reason=voice_audio.missing() or "")
CONFIG = WorkerConfig(
    allowed_roots=(),
    denied_paths=(),
    shell_allowlist=(),
    max_output_chars=4000,
    command_timeout_s=5,
    stt_model_dir=voice_audio.model_dir() or "",
)


def test_engine_streams_partials_and_meets_the_release_budget() -> None:
    from workers.voice.engine import VoskSpeechToText

    stt = VoskSpeechToText(CONFIG.stt_model_dir)
    pcm, rate = voice_audio.speech("what time is it")
    stt.open("s1", rate)
    events: list[TranscriptEvent] = []
    for chunk in voice_audio.chunks(pcm, rate):
        events += stt.feed("s1", chunk)
    released = time.perf_counter()
    final = stt.close("s1")
    release_ms = (time.perf_counter() - released) * 1000
    assert final.text == "what time is it"
    assert any(e.kind == "partial" for e in events)
    assert release_ms < VOICE_RELEASE_TO_FINAL_BUDGET_MS, f"release->final took {release_ms:.0f} ms"


async def test_voice_worker_process_emits_transcripts() -> None:
    worker = WorkerHost("voice", "workers.voice")
    events: list[TranscriptEvent] = []
    worker.on_transcript = events.append
    await worker.start(CONFIG)
    try:
        pcm, rate = voice_audio.speech("open firefox")
        await worker.audio_open("s1", rate)
        for chunk in voice_audio.chunks(pcm, rate):
            await worker.audio_chunk("s1", base64.b64encode(chunk).decode())
        await worker.audio_close("s1")
        for _ in range(100):
            if events and events[-1].kind == "final":
                break
            await asyncio.sleep(0.05)
    finally:
        await worker.stop()
    assert events[-1].kind == "final" and events[-1].text == "open firefox"
    assert any(e.kind == "partial" for e in events[:-1])


async def test_voice_worker_without_a_model_says_so() -> None:
    worker = WorkerHost("voice", "workers.voice")
    events: list[TranscriptEvent] = []
    worker.on_transcript = events.append
    await worker.start(CONFIG.model_copy(update={"stt_model_dir": ""}))
    try:
        await worker.audio_open("s1", 16000)
        for _ in range(40):
            if events:
                break
            await asyncio.sleep(0.05)
    finally:
        await worker.stop()
    assert events and events[0].kind == "final" and events[0].ms < 0


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Harness]:
    models = tmp_path / "models"
    models.mkdir()
    (models / "vosk-en-small").symlink_to(voice_audio.model_dir() or "")
    from core.config import Settings

    settings = Settings()
    settings.voice.stt_model = "vosk-en-small"
    yield from make_harness(tmp_path, FakeProvider(), start_workers=True, settings=settings)


def speak(
    ws: Any, text: str, stream: str = "v1", mode: str = "chat"
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pcm, rate = voice_audio.speech(text)
    ws.send_text(json.dumps({"type": "voice.start", "stream_id": stream, "sample_rate": rate, "mode": mode}))
    for chunk in voice_audio.chunks(pcm, rate):
        time.sleep(0.1)  # like a microphone: 100 ms of audio every 100 ms
        ws.send_text(
            json.dumps(
                {"type": "voice.audio", "stream_id": stream, "pcm_b64": base64.b64encode(chunk).decode()}
            )
        )
    ws.send_text(json.dumps({"type": "voice.stop", "stream_id": stream}))
    seen = []
    while (event := json.loads(ws.receive_text()))["type"] not in {"voice.final", "voice.error"}:
        seen.append(event)
    return seen, event


def test_spoken_instant_command_end_to_end(daemon: Harness) -> None:
    """Hold, say "what time is it", release: final text, then the instant answer, all timed."""
    with daemon.ws() as ws:
        handshake(ws)
        partials, final = speak(ws, "what time is it")
        assert final["type"] == "voice.final" and final["text"] == "what time is it"
        assert any(p["type"] == "voice.partial" for p in partials)
        assert final["release_to_final_ms"] < VOICE_RELEASE_TO_FINAL_BUDGET_MS
        ws.send_text(
            json.dumps(
                {
                    "type": "chat.send",
                    "request_id": "r1",
                    "text": final["text"],
                    "voice": {
                        "stream_id": "v1",
                        "release_to_final_ms": final["release_to_final_ms"],
                        "hotkey_to_first_partial_ms": 120.0,
                    },
                }
            )
        )
        while (done := json.loads(ws.receive_text()))["type"] != "chat.done":
            pass
    print("\nvoice timings:", done["timings"])
    assert done["route"] == "instant"
    timings = done["timings"]
    assert timings["release_to_final_ms"] == final["release_to_final_ms"]
    assert timings["hotkey_to_first_partial_ms"] == 120.0
    first_action = timings["release_to_final_ms"] + timings["final_to_first_action_ms"]
    assert first_action < INSTANT_FIRST_ACTION_BUDGET_MS, timings


def test_voice_status_and_commands(daemon: Harness) -> None:
    status = daemon.client.get("/api/voice", headers=daemon.auth).json()
    assert status["engine_running"] is True
    small = next(m for m in status["models"] if m["id"] == "vosk-en-small")
    assert small["installed"] and small["active"] and small["license"] == "Apache-2.0"
    assert "core.cli voice" in status["shortcut_commands"]["Talk to the assistant"]
    with daemon.ws() as ws:
        handshake(ws)
        assert (
            daemon.client.post(
                "/api/voice/command", headers=daemon.auth, json={"action": "toggle_chat"}
            ).status_code
            == 204
        )
        assert json.loads(ws.receive_text()) == {"type": "voice.command", "action": "toggle_chat"}
