"""The ears process pieces: loudness, resampling, the test microphone, speaking (to a file, with
no voice available, through Piper into a fake sound device), and the process protocol."""

import base64
import json
import os
import subprocess
import sys
import threading
import time
import types
import wave
from pathlib import Path

import pytest

from contracts.ears import EarsSaid, EarsSay, from_ears_adapter
from workers.ears import speaker as speaker_mod
from workers.ears.mic import BLOCK_FRAMES, WavMic, level_of, resample
from workers.ears.speaker import Speaker

BACKEND = Path(__file__).resolve().parents[1]


def _pcm(value: int, frames: int) -> bytes:
    import array

    return array.array("h", [value] * frames).tobytes()


def test_loudness_and_resampling() -> None:
    assert level_of(_pcm(0, 1600)) == 0.0
    assert 0.5 < level_of(_pcm(8000, 1600)) < 1.0
    assert len(resample(_pcm(100, 4800), 48_000)) == 1600 * 2  # 100 ms at 48 kHz -> 16 kHz
    assert resample(b"abcd", 16_000) == b"abcd"
    assert resample(b"", 44_100) == b""


def _wav(path: Path, frames: int, value: int = 9000) -> str:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(_pcm(value, frames))
    return str(path)


def test_the_test_microphone_answers_after_each_spoken_reply(tmp_path: Path) -> None:
    blocks: list[bytes] = []
    mic = WavMic(
        blocks.append, [_wav(tmp_path / "a.wav", BLOCK_FRAMES * 2), _wav(tmp_path / "b.wav", BLOCK_FRAMES)]
    )
    mic.start()
    time.sleep(0.5)
    loud = [b for b in blocks if level_of(b) > 0]
    assert len(loud) == 2  # the first clip, then silence until the assistant has spoken
    mic.advance()
    time.sleep(0.4)
    assert len([b for b in blocks if level_of(b) > 0]) == 3
    mic.stop()
    count = len(blocks)
    time.sleep(0.3)
    assert len(blocks) == count  # closed: nothing is sent


def _speak(speaker_text: str, **say: str) -> EarsSaid:
    done: list[EarsSaid] = []
    event = threading.Event()

    def finished(msg: EarsSaid) -> None:
        done.append(msg)
        event.set()

    Speaker(finished).say(EarsSay(say_id="s1", text=speaker_text, **say))
    assert event.wait(60)
    return done[0]


def test_speech_can_be_recorded_to_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = tmp_path / "spoken.txt"
    monkeypatch.setenv("ASSISTANT_EARS_SINK", str(sink))
    said = _speak("Hello there.")
    assert said.ok and said.voice == "test"
    assert sink.read_text() == "Hello there.\n"


def test_without_any_voice_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASSISTANT_EARS_SINK", raising=False)
    monkeypatch.setattr(speaker_mod, "system_voice_command", lambda: None)
    said = _speak("Hello.")
    assert not said.ok and said.voice == "none" and "no voice" in said.detail


def test_stopping_never_blocks_even_if_the_speech_server_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def hang(argv: list[str], **_kw: object) -> None:
        calls.append(argv)
        time.sleep(10)

    monkeypatch.setattr(speaker_mod.subprocess, "run", hang)
    speaker = Speaker(lambda _m: None)
    speaker._proc = subprocess.Popen(["sleep", "30"])
    speaker._proc.args = ["spd-say", "-w", "-e"]
    started = time.monotonic()
    speaker.stop()
    assert time.monotonic() - started < 1.0
    Speaker(lambda _m: None).stop()  # nothing of ours speaking: it doesn't touch the server at all


PIPER_DIR = os.environ.get("PIPER_TEST_DIR")
"""An installed natural voice (`core.cli install-voice` into a scratch data folder)/piper."""


@pytest.mark.skipif(not PIPER_DIR, reason="set PIPER_TEST_DIR to an installed natural voice")
def test_piper_audio_goes_to_the_sound_device(monkeypatch: pytest.MonkeyPatch) -> None:
    assert PIPER_DIR is not None
    played: list[bytes] = []
    rates: list[int] = []

    class FakeOut:
        def __init__(self, samplerate: int, channels: int, dtype: str) -> None:
            rates.append(samplerate)

        def __enter__(self) -> "FakeOut":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def write(self, data: bytes) -> None:
            assert len(data) % 2 == 0
            played.append(bytes(data))

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawOutputStream=FakeOut))
    monkeypatch.delenv("ASSISTANT_EARS_SINK", raising=False)
    root = Path(PIPER_DIR)
    said = _speak(
        "It is a quarter past six.", piper=str(root / "venv/bin/piper"), voice=str(root / "voice.onnx")
    )
    assert said.ok and said.voice == "piper", said
    assert rates == [22_050]
    assert sum(len(p) for p in played) > 22_050  # over half a second of speech


def test_the_process_reports_and_streams_the_test_microphone(tmp_path: Path) -> None:
    clip = _wav(tmp_path / "a.wav", BLOCK_FRAMES * 3)
    sink = tmp_path / "spoken.txt"
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "ASSISTANT_EARS_WAV": clip,
        "ASSISTANT_EARS_SINK": str(sink),
    }
    proc = subprocess.Popen([sys.executable, "-m", "workers.ears"], cwd=BACKEND, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)  # fmt: skip
    assert proc.stdin is not None and proc.stdout is not None
    try:
        ready = from_ears_adapter.validate_json(proc.stdout.readline())
        assert ready.type == "ears.ready" and ready.available
        proc.stdin.write(b'{"type": "ears.listen", "on": true}\n')
        proc.stdin.write(json.dumps({"type": "ears.say", "say_id": "x", "text": "Hi."}).encode() + b"\n")
        proc.stdin.flush()
        frames, said = [], None
        while said is None or len(frames) < 3:
            msg = from_ears_adapter.validate_json(proc.stdout.readline())
            if msg.type == "ears.frame":
                frames.append(base64.b64decode(msg.pcm_b64))
            elif msg.type == "ears.said":
                said = msg
        assert said.ok and sink.read_text() == "Hi.\n"
        assert all(len(f) == BLOCK_FRAMES * 2 for f in frames)
        proc.stdin.write(b"not json\n")  # ignored, the process carries on
        proc.stdin.flush()
    finally:
        proc.stdin.close()
        assert proc.wait(10) == 0


def test_test_hooks_always_reach_the_ears_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Child processes lose ASSISTANT_* variables; these two must survive for every caller, or a
    test would open the real microphone and play through the real speakers."""
    from core.ears_host import EarsHost

    monkeypatch.setenv("ASSISTANT_EARS_WAV", "/tmp/x.wav")
    monkeypatch.setenv("ASSISTANT_EARS_SINK", "/tmp/spoken.txt")
    env = EarsHost(lambda _p, _l: None)._process._extra_env
    assert env["ASSISTANT_EARS_WAV"] == "/tmp/x.wav" and env["ASSISTANT_EARS_SINK"] == "/tmp/spoken.txt"
