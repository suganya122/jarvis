"""The voice worker's side of Apple speech recognition, against a stand-in for `Jarvis --speech`
that speaks the same line protocol (the real one is Swift and needs a Mac)."""

import stat
import sys
from pathlib import Path

import pytest

from workers.voice.apple import AppleSpeechToText
from workers.voice.engine import SttError

FAKE_HELPER = """#!{python}
import json, sys
print(json.dumps({{"type": "ready", "ok": {ok}, "detail": "fake"}}), flush=True)
heard = {{}}
for line in sys.stdin:
    msg = json.loads(line)
    if msg["type"] == "open":
        heard[msg["id"]] = 0
    elif msg["type"] == "audio" and {dictation_off}:
        print(json.dumps({{"type": "error", "id": msg["id"], "text": "Dictation is off"}}), flush=True)
    elif msg["type"] == "audio":
        heard[msg["id"]] += 1
        words = ["Jarvis", "Jarvis what", "Jarvis what time"][min(heard[msg["id"]], 3) - 1]
        print(json.dumps({{"type": "partial", "id": msg["id"], "text": words}}), flush=True)
    elif msg["type"] == "close" and not msg["abort"]:
        final = {{"type": "final", "id": msg["id"], "text": "Jarvis, what time is it?"}}
        print(json.dumps(final), flush=True)
"""


def helper(tmp_path: Path, ok: bool = True, dictation_off: bool = False) -> str:
    path = tmp_path / "Jarvis"
    path.write_text(FAKE_HELPER.format(python=sys.executable, ok=ok, dictation_off=dictation_off))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _wait_ready(stt: AppleSpeechToText) -> None:
    import time

    for _ in range(100):
        if stt.ready is not None:
            return
        time.sleep(0.05)


@pytest.mark.skipif(sys.platform == "win32", reason="a script helper needs a POSIX shebang")
def test_partials_stream_and_close_gives_the_final_text(tmp_path: Path) -> None:
    import time

    stt = AppleSpeechToText(helper(tmp_path))
    _wait_ready(stt)
    assert stt.ready is True
    stt.open("s1", 16_000)
    seen: list[str] = []
    for _ in range(3):
        seen += [e.text for e in stt.feed("s1", b"\x00\x00" * 1600)]
        time.sleep(0.15)
    seen += [e.text for e in stt.feed("s1", b"\x00\x00" * 10)]
    assert "jarvis what time" in seen  # lower-cased like the other engine
    final = stt.close("s1")
    assert (final.kind, final.text) == ("final", "jarvis, what time is it?")
    with pytest.raises(SttError):
        stt.feed("s1", b"\x00\x00")  # closed


@pytest.mark.skipif(sys.platform == "win32", reason="a script helper needs a POSIX shebang")
def test_without_permission_it_says_how_to_allow_it(tmp_path: Path) -> None:
    stt = AppleSpeechToText(helper(tmp_path, ok=False))
    _wait_ready(stt)
    with pytest.raises(SttError, match="Speech Recognition"):
        stt.open("s1", 16_000)


@pytest.mark.skipif(sys.platform == "win32", reason="a script helper needs a POSIX shebang")
def test_dictation_off_comes_back_as_an_error_not_silence(tmp_path: Path) -> None:
    import time

    stt = AppleSpeechToText(helper(tmp_path, dictation_off=True))
    _wait_ready(stt)
    stt.open("s1", 16_000)
    events = stt.feed("s1", b"\x00\x00" * 1600)
    for _ in range(40):
        if events:
            break
        time.sleep(0.05)
        events = stt.feed("s1", b"\x00\x00" * 10)
    assert [(e.kind, e.error) for e in events] == [("final", "Dictation is off")]


@pytest.mark.skipif(sys.platform == "win32", reason="a script helper needs a POSIX shebang")
def test_a_stopped_helper_is_started_again_instead_of_staying_deaf(tmp_path: Path) -> None:
    """Jarvis.app restarted under it once: "Apple speech recognition stopped" until a restart."""
    stt = AppleSpeechToText(helper(tmp_path))
    _wait_ready(stt)
    stt._proc.kill()
    stt._proc.wait()
    stt.open("s2", 16_000)  # a new helper, not an error
    assert stt.ready is True and stt._proc.poll() is None
