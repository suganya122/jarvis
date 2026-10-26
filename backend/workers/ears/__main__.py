"""The ears process: `python -m workers.ears`, driven by core over stdin/stdout.

Without PortAudio or a microphone it reports `available: false` once and idles (it can still
speak), so core doesn't keep restarting it. `ASSISTANT_EARS_WAV` replaces the microphone with
WAV files and `ASSISTANT_EARS_SINK` records speech to a file: both for tests only.
"""

import base64
import os
import queue
import sys
import threading
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from contracts import CONTRACT_VERSION
from contracts.ears import (
    EarsFrame,
    EarsListen,
    EarsMicChosen,
    EarsMicError,
    EarsReady,
    EarsSaid,
    EarsSay,
    EarsStopSaying,
    FromEars,
    ToEars,
    to_ears_adapter,
)

from . import devices
from .mic import Mic, WavMic, level_of
from .speaker import Speaker


def main() -> None:
    outbox: queue.SimpleQueue[str | None] = queue.SimpleQueue()

    def send(message: FromEars) -> None:
        outbox.put(message.model_dump_json())

    def write_all() -> None:
        while (line := outbox.get()) is not None:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    writer = threading.Thread(target=write_all)
    writer.start()

    def on_pcm(pcm: bytes) -> None:
        send(EarsFrame(pcm_b64=base64.b64encode(pcm).decode("ascii"), level=level_of(pcm)))

    wav = os.environ.get("ASSISTANT_EARS_WAV")
    mic: Mic | WavMic | Any = (
        WavMic(on_pcm, wav.split(os.pathsep))
        if wav
        else Mic(
            on_pcm,
            lambda d: send(EarsMicError(detail=d)),
            lambda m, why: send(EarsMicChosen(microphone=m, reason=why)),
        )
    )

    def said(message: EarsSaid) -> None:
        send(message)
        if isinstance(mic, WavMic):
            mic.advance()

    speaker: Speaker | Any = Speaker(said)
    available, detail = (True, "test microphone") if wav else _probe()
    if (helper := os.environ.get("ASSISTANT_MAC_AUDIO")) and not wav:
        audio = _mac_audio(helper, on_pcm, said)
        if audio is not None:  # one engine for both, with echo cancellation
            mic, speaker = audio.mic, audio.speaker
            available, detail = True, f"{audio.microphone} ({audio.detail})"
            send(EarsMicChosen(microphone=audio.microphone, reason=audio.detail))
    mics = [] if wav else devices.list_microphones()[0]
    send(EarsReady(contract_version=CONTRACT_VERSION, available=available, detail=detail, microphones=mics))

    for raw in sys.stdin.buffer:
        try:
            msg = to_ears_adapter.validate_json(raw)
        except ValidationError:
            continue
        try:
            handle(msg, mic, speaker, send)
        except Exception as exc:  # one bad command must not take the microphone down
            print(f"ears: {type(exc).__name__}: {exc}", file=sys.stderr)
    mic.stop()
    speaker.stop()
    outbox.put(None)
    writer.join(timeout=2)


def handle(msg: ToEars, mic: Any, speaker: Any, send: Callable[[FromEars], None]) -> None:
    if isinstance(msg, EarsListen):
        if not msg.on:
            mic.stop()
        elif (problem := mic.start(msg.microphone)) is not None:
            send(EarsMicError(detail=problem))
    elif isinstance(msg, EarsSay):
        speaker.say(msg)
    elif isinstance(msg, EarsStopSaying):
        speaker.stop()


def _mac_audio(helper: str, on_pcm: Callable[[bytes], None], said: Callable[[EarsSaid], None]) -> Any:
    """Jarvis.app's audio engine, or None (then the plain microphone and `say` are used)."""
    from .mac_audio import MacAudio

    try:
        audio = MacAudio(helper, on_pcm, said, lambda d: print(f"ears: {d}", file=sys.stderr))
    except OSError as exc:
        print(f"ears: Jarvis.app's audio couldn't start ({exc}); using the plain microphone", file=sys.stderr)
        return None
    return audio if audio.alive else None


def _probe() -> tuple[bool, str]:
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        hint = " (Ubuntu/Debian: sudo apt install libportaudio2)" if sys.platform.startswith("linux") else ""
        return False, f"audio isn't available: {exc}{hint}"
    try:
        return True, str(sd.query_devices(kind="input")["name"])
    except Exception:
        return False, "no microphone found"


if __name__ == "__main__":
    main()
