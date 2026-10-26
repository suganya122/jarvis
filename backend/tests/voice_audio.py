"""Test speech: the small Vosk model (path from ASSISTANT_TEST_STT_MODEL) and clips spoken
by a local espeak. Both are test-only and never shipped; tests skip without them."""

import io
import os
import shutil
import subprocess
import wave
from pathlib import Path

MODEL_ENV = "ASSISTANT_TEST_STT_MODEL"


def model_dir() -> str | None:
    path = os.environ.get(MODEL_ENV, "")
    return path if path and (Path(path) / "conf").is_dir() else None


def espeak() -> str | None:
    return shutil.which("espeak-ng") or shutil.which("espeak")


def missing() -> str | None:
    if model_dir() is None:
        return f"set {MODEL_ENV} to an unpacked vosk-model-small-en-us-0.15"
    if espeak() is None:
        return "espeak or espeak-ng is needed to make test speech"
    return None


def speech(text: str) -> tuple[bytes, int]:
    """PCM16 mono audio of `text`, plus its sample rate."""
    tool = espeak()
    assert tool is not None
    wav = subprocess.run([tool, "-s", "150", "--stdout", text], capture_output=True, check=True).stdout
    with wave.open(io.BytesIO(wav)) as w:
        return w.readframes(w.getnframes()), w.getframerate()


def chunks(pcm: bytes, rate: int, ms: int = 100) -> list[bytes]:
    size = rate * 2 * ms // 1000
    return [pcm[i : i + size] for i in range(0, len(pcm), size)]
