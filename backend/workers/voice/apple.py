"""macOS speech recognition through Apple's Speech framework, on the device.

The work happens in Jarvis.app's own executable started as `Jarvis --speech` (Swift; see
core/macos/Speech.swift): this process sends it 16 kHz audio and reads back transcripts, one JSON
object per line. Running inside Jarvis.app means macOS asks once for Speech Recognition and
microphone permission for "Jarvis". Nothing is sent to Apple's servers: recognition is
on-device (`requiresOnDeviceRecognition`).
"""

import base64
import contextlib
import json
import queue
import subprocess
import threading
import time
from typing import IO, Any

from contracts.voice import TranscriptEvent
from workers.voice.engine import SttError

CLOSE_WAIT_S = 4.0
PERMISSION = "Allow Speech Recognition for Jarvis: System Settings → Privacy & Security → Speech Recognition"


class AppleSpeechToText:
    def __init__(self, helper: str) -> None:
        self._helper = helper
        self._events: dict[str, queue.Queue[TranscriptEvent]] = {}
        self._lock = threading.Lock()
        self._start()

    def _start(self) -> None:
        """(Re)start the helper. If it's stopped (Jarvis.app restarted, a crash), the next request
        starts a new one instead of the assistant staying deaf."""
        self._proc = subprocess.Popen(  # noqa: S603 - our own app's executable
            [self._helper, "--speech"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )  # its notes (stderr) go to the worker's, and from there to the log
        self.ready: bool | None = None
        self.detail = "waiting for Speech Recognition permission"
        threading.Thread(target=self._read, args=(self._proc.stdout,), daemon=True).start()

    def _read(self, stream: IO[bytes] | None) -> None:
        assert stream is not None
        for raw in stream:
            try:
                msg: dict[str, Any] = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "ready":
                self.ready, self.detail = bool(msg.get("ok")), str(msg.get("detail", ""))
                continue
            stream_id = str(msg.get("id", ""))
            kind = msg.get("type")
            if kind == "error" and stream_id in self._events:  # e.g. Dictation is off: say why
                problem = str(msg.get("text", ""))
                self._events[stream_id].put(
                    TranscriptEvent(stream_id=stream_id, kind="final", text="", ms=-1, error=problem)
                )
                continue
            if kind in ("partial", "final") and stream_id in self._events:
                event = TranscriptEvent(stream_id=stream_id, kind=kind, text=str(msg.get("text", "")).lower())
                self._events[stream_id].put(event)

    def _send(self, message: dict[str, Any]) -> None:
        if self._proc.poll() is not None or self._proc.stdin is None:
            raise SttError("Apple speech recognition stopped")
        with self._lock:
            self._proc.stdin.write(json.dumps(message).encode() + b"\n")
            self._proc.stdin.flush()

    def open(self, stream_id: str, sample_rate: int) -> None:
        if self._proc.poll() is not None:
            self._events.clear()
            self._start()
            for _ in range(50):  # its answer to the permission question comes first
                if self.ready is not None:
                    break
                time.sleep(0.05)
        if self.ready is False:
            raise SttError(f"{PERMISSION} ({self.detail})")
        self._events[stream_id] = queue.Queue()
        self._send({"type": "open", "id": stream_id, "rate": sample_rate})

    def feed(self, stream_id: str, pcm16: bytes) -> list[TranscriptEvent]:
        if stream_id not in self._events:
            raise SttError("unknown audio stream")
        self._send({"type": "audio", "id": stream_id, "pcm_b64": base64.b64encode(pcm16).decode("ascii")})
        return self._drain(stream_id)

    def _drain(self, stream_id: str) -> list[TranscriptEvent]:
        found: list[TranscriptEvent] = []
        events = self._events.get(stream_id)
        while events is not None and not events.empty():
            event = events.get_nowait()
            if event.kind == "final" and not event.error:  # finished on its own: keep it as a segment
                event = event.model_copy(update={"kind": "segment"})
            found.append(event)
        return found

    def close(self, stream_id: str) -> TranscriptEvent:
        events = self._events.get(stream_id)
        if events is None:
            raise SttError("unknown audio stream")
        self._send({"type": "close", "id": stream_id, "abort": False})
        started, text = time.monotonic(), ""
        try:
            while time.monotonic() - started < CLOSE_WAIT_S:
                try:
                    event = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                text = event.text or text
                if event.error:
                    raise SttError(event.error)
                if event.kind == "final":
                    break
        finally:
            self._events.pop(stream_id, None)
        return TranscriptEvent(
            stream_id=stream_id, kind="final", text=text, ms=(time.monotonic() - started) * 1000
        )

    def abort(self, stream_id: str) -> None:
        if self._events.pop(stream_id, None) is not None:
            with contextlib.suppress(SttError):
                self._send({"type": "close", "id": stream_id, "abort": True})
