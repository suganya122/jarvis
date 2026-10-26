"""Hands-free end to end: the real daemon (`python -m core`), the real ears process with the
test microphone playing recorded speech, the real Vosk speech worker, and the fake model.
Speech goes to a file instead of the speakers, so nothing touches this computer's audio.

Needs a downloaded speech model: it borrows one from this computer's app data folder
(read-only, through a link), or from ASSISTANT_TEST_STT_MODEL. Skipped otherwise.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from core import paths

BACKEND = Path(__file__).resolve().parents[1]
CLIPS = BACKEND / "tests" / "fixtures" / "voice"


def _model() -> tuple[str, Path] | None:
    explicit = os.environ.get("ASSISTANT_TEST_STT_MODEL")
    if explicit:  # CI: a folder with the small English model in it
        path = Path(explicit)
        model_id = os.environ.get("ASSISTANT_TEST_STT_MODEL_ID", "vosk-en-small")
        return (model_id, path) if (path / "conf").is_dir() else None
    for model_id in ("vosk-en-small", "vosk-en-standard"):
        path = paths.data_dir() / "models" / model_id
        if (path / "conf").is_dir():
            return model_id, path
    return None


MODEL = _model()
pytestmark = pytest.mark.skipif(MODEL is None, reason="needs a downloaded speech model")


class Daemon:
    def __init__(self, root: Path, clips: list[Path]) -> None:
        assert MODEL is not None
        self.data = root / "data"
        (self.data / "models").mkdir(parents=True)
        (self.data / "models" / MODEL[0]).symlink_to(MODEL[1], target_is_directory=True)
        self.sink = root / "spoken.txt"
        self.log = root / "daemon.log"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        env = {
            **os.environ,
            "ASSISTANT_DATA_DIR": str(self.data),
            "ASSISTANT_EARS_WAV": os.pathsep.join(map(str, clips)),
            "ASSISTANT_EARS_SINK": str(self.sink),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "BROWSER": "true",
        }
        env.pop("ANTHROPIC_API_KEY", None)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "core", "--port", str(self.port), "--fake-model", "--no-overlay"],
            cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=self.log.open("wb"),
        )  # fmt: skip
        self.wait("the daemon", self._up, 30)
        self.token = (self.data / "auth_token").read_text().strip()

    def _up(self) -> bool:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=1)
            return True
        except OSError:
            return False

    def api(self, method: str, path: str, body: object | None = None) -> object:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            method=method,
            data=json.dumps(body).encode() if body else None,
        )
        req.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read() or b"null")

    def state(self) -> str:
        return str(self.api("GET", "/api/voice")["hands_free"]["state"])  # type: ignore[index]

    def spoken(self) -> list[str]:
        return self.sink.read_text().splitlines() if self.sink.exists() else []

    def wait(self, what: str, cond: object, timeout: float = 40) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if cond():  # type: ignore[operator]
                return
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for {what}; spoken={self.spoken()}")

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=20)


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Daemon]:
    d = Daemon(tmp_path, [CLIPS / "wake_time.wav", CLIPS / "thanks.wav"])
    yield d
    d.stop()


def test_a_spoken_conversation_end_to_end(daemon: Daemon) -> None:
    assert daemon.state() == "off"  # no speech model chosen yet: the microphone stays closed
    assert MODEL is not None
    settings = daemon.api("GET", "/api/settings")
    settings["voice"]["stt_model"] = MODEL[0]  # type: ignore[index]
    daemon.api("PUT", "/api/settings", settings)

    # First start: a spoken hello (and the box), so you know it's there
    daemon.wait("the hello", lambda: daemon.spoken()[:1] == ["Jarvis is ready."])

    # "Jarvis, what time is it?" -> the time, spoken (an instant command: no AI call)
    daemon.wait("the spoken time", lambda: any(line.startswith("It's ") for line in daemon.spoken()))
    reply = next(line for line in daemon.spoken() if line.startswith("It's "))
    assert reply.startswith("It's ") and any(ch.isdigit() for ch in reply), reply
    assert "UTC+" not in reply and not any(f"-{m:02d}-" in reply for m in range(1, 13)), (
        reply
    )  # said like a person

    # "That's all, thanks." (a follow-up, no wake word) -> "Okay.", then back to waiting
    daemon.wait("'Okay.'", lambda: daemon.spoken()[-1:] == ["Okay."])
    daemon.wait("back to waiting", lambda: daemon.state() == "idle", timeout=30)

    sessions = daemon.api("GET", "/api/sessions")
    assert [s["title"] for s in sessions] == ["what time is it"]  # type: ignore[union-attr,index]
    actions = [e["action"] for e in daemon.api("GET", "/api/audit?limit=100")]  # type: ignore[union-attr,index]
    assert "voice.hands_free" in actions
    status = daemon.api("GET", "/api/voice")["hands_free"]  # type: ignore[index]
    assert status["microphone"].startswith("test microphone") or status["microphone"] == ""

    daemon.stop()
    log = daemon.log.read_text(errors="replace").lower()
    assert "what time" not in log and "that's all" not in log  # nothing heard is logged
