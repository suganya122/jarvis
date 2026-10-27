"""The ears worker's macOS audio (mic and voice in one engine, echo cancelled) against a stand-in for
`Jarvis --audio` that speaks the same line protocol (the real one is Swift and needs a Mac)."""

import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from contracts.ears import EarsSaid, EarsSay
from workers.ears.mac_audio import MacAudio

FAKE_HELPER = """#!{python}
import base64, json, sys, threading
def out(m):
    print(json.dumps(m), flush=True)
out({{"type": "ready", "echo_cancel": True, "microphone": "MacBook Air Microphone",
     "detail": "echo cancellation on"}})
saying = {{}}
def finish(i):
    if saying.pop(i, None):
        out({{"type": "said", "id": i, "ok": True, "detail": ""}})
for line in sys.stdin:
    m = json.loads(line)
    if m["type"] == "listen" and m["on"]:
        out({{"type": "frame", "pcm_b64": base64.b64encode(b"\\x01\\x00" * 1600).decode()}})
    elif m["type"] == "say":
        saying[m["id"]] = True
        threading.Timer(0.3, finish, args=(m["id"],)).start()
    elif m["type"] == "stop":
        for i in list(saying):
            saying.pop(i)
            out({{"type": "said", "id": i, "ok": False, "detail": "stopped"}})
"""

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="a script helper needs a POSIX shebang")


def start(tmp_path: Path) -> tuple[MacAudio, list[bytes], list[EarsSaid]]:
    path = tmp_path / "Jarvis"
    path.write_text(FAKE_HELPER.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    frames: list[bytes] = []
    said: list[EarsSaid] = []
    return MacAudio(str(path), frames.append, said.append, lambda _d: None), frames, said


def until(condition: object, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:  # type: ignore[operator]
        time.sleep(0.02)


def test_it_reports_echo_cancellation_and_the_microphone(tmp_path: Path) -> None:
    audio, frames, _ = start(tmp_path)
    assert audio.echo_cancel and audio.microphone == "MacBook Air Microphone"
    assert audio.mic.start() is None
    until(lambda: frames)
    assert frames[0] == b"\x01\x00" * 1600  # 100 ms of 16 kHz audio, as the plain mic sends
    audio.close()


def test_speech_is_the_macs_own_say_not_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Playing speech through the voice-processing engine muffled it and swapped the voice the
    person had chosen; speech goes through `say` (recorded to a file here, never played)."""
    sink = tmp_path / "said.txt"
    monkeypatch.setenv("ASSISTANT_EARS_SINK", str(sink))
    audio, _, said = start(tmp_path)
    audio.speaker.say(EarsSay(say_id="a", text="It is noon."))
    until(lambda: said)
    assert (said[0].say_id, said[0].ok, said[0].voice) == ("a", True, "test")
    assert sink.read_text() == "It is noon.\n"
    audio.close()
    audio.speaker.say(EarsSay(say_id="b", text="Still speaks."))  # even with the engine gone
    until(lambda: len(said) == 2)
    assert said[1].ok


def test_the_ears_process_starts_with_it_and_hears(tmp_path: Path) -> None:
    """The whole ears process, not just the adapter: it once crashed at start-up reporting the
    microphone, so the Mac heard nothing at all."""
    import json
    import os
    import subprocess

    helper = tmp_path / "Jarvis"
    helper.write_text(FAKE_HELPER.format(python=sys.executable))
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "ASSISTANT_MAC_AUDIO": str(helper), "PYTHONPATH": str(Path(__file__).parents[1])}
    env.pop("ASSISTANT_EARS_WAV", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "workers.ears"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps({"type": "ears.listen", "on": True}) + "\n")
    proc.stdin.flush()
    import queue

    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x) for x in proc.stdout], daemon=True).start()  # type: ignore[union-attr]
    seen: list[str] = []
    deadline = time.monotonic() + 10
    while "ears.frame" not in seen and time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            if proc.poll() is not None:
                break  # it exited (a start-up crash): fail below, don't hang
            continue
        message = json.loads(line)
        seen.append(message["type"])
        if message["type"] == "ears.mic_chosen":
            assert message["microphone"]["name"] == "MacBook Air Microphone"
    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    assert seen[:2] == ["ears.ready", "ears.mic_chosen"], (seen, proc.stderr.read() if proc.stderr else "")
    assert "ears.frame" in seen
