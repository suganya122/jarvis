"""Protocol between core and a worker process: one JSON object per line over stdin/stdout.

Pipes are private to the two processes, so no extra auth is needed. Workers receive no
secrets: core strips API keys and tokens from the worker's environment.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from contracts.common import ContractModel
from contracts.tools import ToolAssessment, ToolInfo, ToolResult
from contracts.voice import TranscriptEvent


class BrowserConfig(ContractModel):
    mode: Literal["isolated", "own"] = "isolated"
    profile_dir: str
    """The isolated profile (cookies, logins made there) inside the app data folder."""
    headless: bool = True
    channel: str = "auto"
    cdp_url: str = "http://127.0.0.1:9222"
    allow_private_hosts: bool = False
    min_seconds_between_pages: float = 1.0
    max_text_chars: int = 4_000


class WorkerConfig(ContractModel):
    allowed_roots: tuple[str, ...]
    """Absolute folders the tools may touch."""
    denied_paths: tuple[str, ...]
    """Absolute paths that are always off limits, even inside an allowed root."""
    shell_allowlist: tuple[str, ...]
    full_control: bool = False
    """run_shell may run any command (see ToolSettings.full_control)."""
    max_output_chars: int
    command_timeout_s: float
    stt_model_dir: str = ""
    """Installed speech-to-text model for the voice worker ('' = none yet)."""
    apple_speech_helper: str = ""
    """macOS: Jarvis.app's executable, which recognises speech on-device with Apple's Speech
    framework (`--speech`). When set, the voice worker uses it instead of Vosk."""
    mac_app: str = ""
    """macOS: Jarvis.app's executable. The hands worker runs it as `--hands` for other apps'
    windows and controls (Accessibility), whichever speech engine is chosen."""
    capture_max_edge: int = 1280
    """Longest side of screenshots sent to the model (cost and privacy)."""
    blocked_apps: tuple[str, ...] = ()
    """App names (case-insensitive substrings) the hands worker must never read or touch."""
    browser: BrowserConfig | None = None
    """For the browser worker."""


# ---- core -> worker ----------------------------------------------------------


class Configure(ContractModel):
    type: Literal["configure"] = "configure"
    config: WorkerConfig


class Assess(ContractModel):
    type: Literal["assess"] = "assess"
    id: str
    tool: str
    args: dict[str, Any]


class Invoke(ContractModel):
    type: Literal["invoke"] = "invoke"
    id: str
    tool: str
    args: dict[str, Any]


class Cancel(ContractModel):
    """Abort a running invoke (kill switch or user cancel)."""

    type: Literal["cancel"] = "cancel"
    id: str


class AudioOpen(ContractModel):
    type: Literal["audio.open"] = "audio.open"
    id: str
    sample_rate: int = Field(ge=8_000, le=48_000)


class AudioChunk(ContractModel):
    type: Literal["audio.chunk"] = "audio.chunk"
    id: str
    pcm_b64: str = Field(max_length=200_000)
    """16-bit little-endian mono PCM."""


class AudioClose(ContractModel):
    type: Literal["audio.close"] = "audio.close"
    id: str
    abort: bool = False


ToWorker = Annotated[
    Configure | Assess | Invoke | Cancel | AudioOpen | AudioChunk | AudioClose, Field(discriminator="type")
]
to_worker_adapter: TypeAdapter[ToWorker] = TypeAdapter(ToWorker)


# ---- worker -> core ----------------------------------------------------------


class Ready(ContractModel):
    type: Literal["ready"] = "ready"
    contract_version: str
    tools: tuple[ToolInfo, ...]


class Assessed(ContractModel):
    type: Literal["assessed"] = "assessed"
    id: str
    assessment: ToolAssessment


class Outcome(ContractModel):
    type: Literal["outcome"] = "outcome"
    id: str
    result: ToolResult


class Failure(ContractModel):
    type: Literal["failure"] = "failure"
    id: str
    error: str


class Transcript(ContractModel):
    """Unsolicited stream event from the voice worker."""

    type: Literal["transcript"] = "transcript"
    event: TranscriptEvent


FromWorker = Annotated[Ready | Assessed | Outcome | Failure | Transcript, Field(discriminator="type")]
from_worker_adapter: TypeAdapter[FromWorker] = TypeAdapter(FromWorker)
