"""Voice contracts: streaming speech-to-text and text-to-speech. Audio never leaves the machine.

The browser captures the microphone (localhost only) and streams 16 kHz mono PCM to
core, which forwards it to the voice worker's SpeechToText engine.
"""

from typing import Literal, Protocol, runtime_checkable

from contracts.common import ContractModel

SAMPLE_RATE = 16_000
MAX_STREAM_SECONDS = 120


class TranscriptEvent(ContractModel):
    stream_id: str
    kind: Literal["partial", "segment", "final"]
    """partial: may still change. segment: the speaker paused (text so far is stable). final: released."""
    text: str
    ms: float = 0.0
    """Engine processing time for the chunk that produced this event."""
    error: str = ""
    """On a final: why recognition stopped, said so a person can fix it (e.g. Dictation is off)."""


@runtime_checkable
class SpeechToText(Protocol):
    def open(self, stream_id: str, sample_rate: int) -> None: ...

    def feed(self, stream_id: str, pcm16: bytes) -> list[TranscriptEvent]: ...

    def close(self, stream_id: str) -> TranscriptEvent:
        """Flush and return the final transcript."""
        ...

    def abort(self, stream_id: str) -> None: ...


@runtime_checkable
class Playback(Protocol):
    async def stop(self) -> None: ...

    async def wait(self) -> None: ...


@runtime_checkable
class TextToSpeech(Protocol):
    async def speak(self, text: str) -> Playback: ...
