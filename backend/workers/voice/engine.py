"""Vosk streaming speech-to-text (Apache-2.0 engine and models; no espeak, no GPL)."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts.voice import MAX_STREAM_SECONDS, TranscriptEvent


class SttError(Exception):
    pass


@dataclass
class _Stream:
    recognizer: Any
    sample_rate: int
    segments: list[str] = field(default_factory=list)
    last_text: str = ""
    bytes_in: int = 0


class VoskSpeechToText:
    def __init__(self, model_dir: str) -> None:
        if not model_dir or not (Path(model_dir) / "conf").is_dir():
            raise SttError("no speech model installed yet; download one in Settings → Voice")
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)
        self._model = Model(model_dir)
        self._streams: dict[str, _Stream] = {}

    def open(self, stream_id: str, sample_rate: int) -> None:
        from vosk import KaldiRecognizer

        self._streams[stream_id] = _Stream(KaldiRecognizer(self._model, sample_rate), sample_rate)

    def _text(self, stream: _Stream, tail: str) -> str:
        return " ".join(s for s in (*stream.segments, tail) if s).strip()

    def feed(self, stream_id: str, pcm16: bytes) -> list[TranscriptEvent]:
        stream = self._streams.get(stream_id)
        if stream is None:
            raise SttError("unknown audio stream")
        stream.bytes_in += len(pcm16)
        if stream.bytes_in > MAX_STREAM_SECONDS * stream.sample_rate * 2:
            raise SttError(f"recordings are limited to {MAX_STREAM_SECONDS} seconds")
        started = time.perf_counter()
        endpoint = stream.recognizer.AcceptWaveform(pcm16)
        ms = (time.perf_counter() - started) * 1000
        if endpoint:  # the speaker paused: this part is final
            segment = json.loads(stream.recognizer.Result()).get("text", "")
            if segment:
                stream.segments.append(segment)
            stream.last_text = self._text(stream, "")
            return [TranscriptEvent(stream_id=stream_id, kind="segment", text=stream.last_text, ms=ms)]
        text = self._text(stream, json.loads(stream.recognizer.PartialResult()).get("partial", ""))
        if text == stream.last_text:
            return []
        stream.last_text = text
        return [TranscriptEvent(stream_id=stream_id, kind="partial", text=text, ms=ms)]

    def close(self, stream_id: str) -> TranscriptEvent:
        stream = self._streams.pop(stream_id, None)
        if stream is None:
            raise SttError("unknown audio stream")
        started = time.perf_counter()
        tail = json.loads(stream.recognizer.FinalResult()).get("text", "")
        ms = (time.perf_counter() - started) * 1000
        return TranscriptEvent(stream_id=stream_id, kind="final", text=self._text(stream, tail), ms=ms)

    def abort(self, stream_id: str) -> None:
        self._streams.pop(stream_id, None)
