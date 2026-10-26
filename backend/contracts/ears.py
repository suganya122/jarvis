"""The ears: the process that owns the microphone and the speaker.

Core runs it as a separate process (workers/ears) and talks over stdin/stdout, one JSON
object per line. It only moves sound: it sends microphone blocks (16 kHz mono 16-bit) and
plays text that core asks it to say. Deciding what to do with either stays in core. Audio
goes nowhere else: not to disk, not to the network.
"""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from contracts.common import ContractModel

EARS_SAMPLE_RATE = 16_000
MAX_SAY_CHARS = 2_000


class Microphone(ContractModel):
    id: str
    """PipeWire node name on Linux; the device name on macOS and Windows."""
    name: str


class EarsListen(ContractModel):
    """Open (or close) the microphone. Closed, the OS shows no microphone in use."""

    type: Literal["ears.listen"] = "ears.listen"
    on: bool
    microphone: str = ""
    """A Microphone.id, or "" to choose automatically (skipping inputs that are only static or silence)."""


class EarsSay(ContractModel):
    type: Literal["ears.say"] = "ears.say"
    say_id: str
    text: str = Field(max_length=MAX_SAY_CHARS)
    piper: str = ""
    """Path to the Piper program, when the natural voice is installed."""
    voice: str = ""
    """Path to the Piper voice model (.onnx)."""


class EarsStopSaying(ContractModel):
    type: Literal["ears.stop_saying"] = "ears.stop_saying"


ToEars = Annotated[EarsListen | EarsSay | EarsStopSaying, Field(discriminator="type")]
to_ears_adapter: TypeAdapter[ToEars] = TypeAdapter(ToEars)


class EarsReady(ContractModel):
    type: Literal["ears.ready"] = "ears.ready"
    contract_version: str
    available: bool
    detail: str = ""
    """Why it can't listen (no PortAudio, no microphone), or which devices it uses."""
    microphones: list[Microphone] = []


class EarsFrame(ContractModel):
    """100 ms of microphone audio, and how loud it was (0-1)."""

    type: Literal["ears.frame"] = "ears.frame"
    pcm_b64: str = Field(max_length=20_000)
    level: float = Field(ge=0.0, le=1.0)


class EarsSaid(ContractModel):
    type: Literal["ears.said"] = "ears.said"
    say_id: str
    ok: bool
    detail: str = ""
    voice: str = ""
    """Which voice spoke: "piper", "system" or "none"."""


class EarsMicChosen(ContractModel):
    """Which microphone is open, and why that one."""

    type: Literal["ears.mic_chosen"] = "ears.mic_chosen"
    microphone: Microphone
    reason: str


class EarsMicError(ContractModel):
    """The microphone failed after starting (unplugged, taken by another app)."""

    type: Literal["ears.mic_error"] = "ears.mic_error"
    detail: str


FromEars = Annotated[
    EarsReady | EarsFrame | EarsSaid | EarsMicChosen | EarsMicError, Field(discriminator="type")
]
from_ears_adapter: TypeAdapter[FromEars] = TypeAdapter(FromEars)
