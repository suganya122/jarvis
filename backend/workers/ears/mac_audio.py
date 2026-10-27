"""macOS: the microphone and the voice through Jarvis.app (`Jarvis --audio`, core/macos/Audio.swift).

Both run in one audio engine with Apple's voice processing on, so what the assistant says is
taken out of what the microphone hears: the person can talk over it without it hearing itself.
`MacAudio.mic` and `MacAudio.speaker` stand in for `Mic` and `Speaker` (same calls), so the rest
of the ears process doesn't change. If voice processing can't run (a mic and speaker on different
devices, say), the helper carries on without it and reports why.
"""

import base64
import contextlib
import json
import os
import subprocess
import threading
from collections.abc import Callable
from typing import IO, Any

from contracts.ears import EarsSaid, EarsSay

READY_WAIT_S = 10.0


class MacAudio:
    def __init__(
        self,
        helper: str,
        on_pcm: Callable[[bytes], None],
        on_said: Callable[[EarsSaid], None],
        on_problem: Callable[[str], None],
    ) -> None:
        self._on_pcm = on_pcm
        self._on_said = on_said
        self._on_problem = on_problem
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closing = False
        self.echo_cancel = False
        self.microphone = ""
        self.detail = "starting"
        self._proc = subprocess.Popen(  # noqa: S603 - our own app's executable
            [helper, "--audio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        threading.Thread(target=self._read, args=(self._proc.stdout,), daemon=True).start()
        self._ready.wait(READY_WAIT_S)
        self.mic = _Mic(self)
        self.speaker = _Speaker(self)

    def _read(self, stream: IO[bytes] | None) -> None:
        assert stream is not None
        for raw in stream:
            try:
                msg: dict[str, Any] = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "frame":
                self._on_pcm(base64.b64decode(msg.get("pcm_b64", "")))
            elif kind == "said":
                say_id, detail = str(msg.get("id", "")), str(msg.get("detail", ""))
                self._on_said(EarsSaid(say_id=say_id, ok=bool(msg.get("ok")), detail=detail, voice="apple"))
            elif kind == "ready":
                self.echo_cancel = bool(msg.get("echo_cancel"))
                self.microphone = str(msg.get("microphone", ""))
                self.detail = str(msg.get("detail", ""))
                self._ready.set()
            elif kind == "problem":
                self.detail = str(msg.get("detail", ""))
                self._on_problem(self.detail)
        self._ready.set()  # it exited: don't keep anyone waiting
        if not self._closing:  # stopped under us: exit, so core starts the ears afresh with a new one
            self._on_problem("Jarvis.app's audio stopped; restarting the ears")
            os._exit(3)

    def send(self, message: dict[str, Any]) -> bool:
        if self._proc.poll() is not None or self._proc.stdin is None:
            return False
        with self._lock, contextlib.suppress(OSError):
            self._proc.stdin.write(json.dumps(message).encode() + b"\n")
            self._proc.stdin.flush()
            return True
        return False

    def said(self, message: EarsSaid) -> None:
        self._on_said(message)

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def close(self) -> None:
        self._closing = True
        with contextlib.suppress(OSError):
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._proc.wait(timeout=2)
        if self._proc.poll() is None:
            self._proc.kill()


class _Mic:
    def __init__(self, audio: MacAudio) -> None:
        self._audio = audio

    def start(self, microphone: str = "") -> str | None:
        """The Mac's chosen input (voice processing works on the system's devices, not a pick)."""
        if not self._audio.send({"type": "listen", "on": True}):
            return "Jarvis.app's audio stopped; restart Jarvis"
        return None

    def stop(self) -> None:
        self._audio.send({"type": "listen", "on": False})


class _Speaker:
    def __init__(self, audio: MacAudio) -> None:
        self._audio = audio

    def say(self, msg: EarsSay) -> None:
        if not self._audio.send({"type": "say", "id": msg.say_id, "text": msg.text}):
            detail = "Jarvis.app's audio stopped; restart Jarvis"
            self._audio.said(EarsSaid(say_id=msg.say_id, ok=False, detail=detail, voice="none"))

    def stop(self) -> None:
        self._audio.send({"type": "stop"})
