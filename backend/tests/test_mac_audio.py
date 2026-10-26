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


def test_speaking_reports_when_it_is_done_and_stop_cuts_it_off(tmp_path: Path) -> None:
    audio, _, said = start(tmp_path)
    audio.speaker.say(EarsSay(say_id="a", text="It is noon."))
    until(lambda: said)
    assert (said[0].say_id, said[0].ok, said[0].voice) == ("a", True, "apple")
    audio.speaker.say(EarsSay(say_id="b", text="A much longer answer."))
    threading.Event().wait(0.05)
    audio.speaker.stop()
    until(lambda: len(said) == 2)
    assert (said[1].say_id, said[1].ok, said[1].detail) == ("b", False, "stopped")
    audio.close()


def test_if_the_helper_is_gone_a_say_fails_at_once_instead_of_hanging(tmp_path: Path) -> None:
    audio, _, said = start(tmp_path)
    audio.close()
    audio.speaker.say(EarsSay(say_id="c", text="Hello."))
    assert said and said[-1].say_id == "c" and not said[-1].ok
