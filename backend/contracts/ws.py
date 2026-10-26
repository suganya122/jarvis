"""WebSocket protocol between the UI (or any channel client) and core.

Mirrored by hand in frontend/src/contracts.ts; tests/test_contracts.py guards drift.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from contracts.automation import TaskInfo
from contracts.capture import HighlightBox
from contracts.channel import ChannelState
from contracts.common import ContractModel, RiskLevel
from contracts.model import ModelErrorCode, ModelTier, Usage
from contracts.skills import SkillNote

MAX_CHAT_TEXT = 32_000
WS_SUBPROTOCOL = "assistant.v1"
WS_TOKEN_PREFIX = "token."  # noqa: S105 - a prefix, not a secret
"""Browsers cannot set headers on WebSockets, so the token rides in a subprotocol."""


# ---- client -> server -------------------------------------------------------


class VoiceTiming(ContractModel):
    stream_id: str
    hotkey_to_first_partial_ms: float | None = None
    release_to_final_ms: float | None = None


class ChatSend(ContractModel):
    type: Literal["chat.send"] = "chat.send"
    request_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = None
    text: str = Field(min_length=1, max_length=MAX_CHAT_TEXT)
    tier: ModelTier | None = None
    force_model: bool = False
    """Skip the instant-command router and ask the model."""
    voice: VoiceTiming | None = None
    """Set when the text came from speech; lets core reuse a speculative start and log latency."""
    attach: Literal["none", "screen", "select"] = "none"
    background: bool = False
    """Run as a background task: returns at once, progress arrives as task.update."""
    """Attach a screenshot: the whole screen, or a region/window the user picks."""


class ChatCancel(ContractModel):
    type: Literal["chat.cancel"] = "chat.cancel"
    request_id: str


class Ping(ContractModel):
    type: Literal["ping"] = "ping"


VoiceMode = Literal["chat", "dictate_composer", "dictate_app"]
"""chat: the transcript becomes a message. dictate_*: cleaned-up text goes into the
message box, or into the focused field of another app."""


class VoiceStart(ContractModel):
    type: Literal["voice.start"] = "voice.start"
    stream_id: str = Field(min_length=1, max_length=64)
    sample_rate: int = Field(default=16_000, ge=8_000, le=48_000)
    mode: VoiceMode = "chat"
    session_id: str | None = None
    tier: ModelTier | None = None


class VoiceAudio(ContractModel):
    type: Literal["voice.audio"] = "voice.audio"
    stream_id: str
    pcm_b64: str = Field(max_length=60_000)


class VoiceStop(ContractModel):
    """Released: finish the transcript."""

    type: Literal["voice.stop"] = "voice.stop"
    stream_id: str
    cancel: bool = False


class ApprovalRespond(ContractModel):
    type: Literal["approval.respond"] = "approval.respond"
    approval_id: str
    approved: bool
    remember: bool = False
    """Allow this tool for the rest of the chat (only honoured when the request allowed it)."""


class StopAll(ContractModel):
    """Kill switch: cancel every running turn and deny every pending approval."""

    type: Literal["control.stop_all"] = "control.stop_all"


class SetPaused(ContractModel):
    type: Literal["control.pause"] = "control.pause"
    paused: bool


class TaskControl(ContractModel):
    type: Literal["task.control"] = "task.control"
    task_id: str
    action: Literal["cancel", "resume", "discard"]


ClientMessage = Annotated[
    ChatSend
    | ChatCancel
    | Ping
    | ApprovalRespond
    | StopAll
    | SetPaused
    | VoiceStart
    | VoiceAudio
    | VoiceStop
    | TaskControl,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# ---- server -> client -------------------------------------------------------


class Hello(ContractModel):
    type: Literal["hello"] = "hello"
    app_name: str
    app_version: str
    contract_version: str


class ChatStarted(ContractModel):
    type: Literal["chat.started"] = "chat.started"
    request_id: str
    trace_id: str
    session_id: str
    tier: ModelTier
    target: str | None = None
    """Voice turns: what "this"/"here" refers to, e.g. 'Screen 2 · Chrome · Inbox'."""


class ChatDelta(ContractModel):
    type: Literal["chat.delta"] = "chat.delta"
    request_id: str
    text: str


class ChatThinking(ContractModel):
    type: Literal["chat.thinking"] = "chat.thinking"
    request_id: str


class ChatDone(ContractModel):
    type: Literal["chat.done"] = "chat.done"
    request_id: str
    model: str
    stop_reason: str
    usage: Usage
    cost_usd: float
    latency_ms: float
    first_token_ms: float | None
    route: Literal["instant", "skill", "model"] = "model"
    """'instant' = rule router, 'skill' = learned skill replay; both make no LLM call."""
    timings: dict[str, float] = {}
    """Per-stage milliseconds, e.g. route_ms, action_ms."""
    skill: SkillNote | None = None
    escalated: str | None = None
    """Why the turn moved from the Cheap tier to Balanced part-way (None: it didn't)."""


class ChatCancelled(ContractModel):
    type: Literal["chat.cancelled"] = "chat.cancelled"
    request_id: str


ServerErrorCode = (
    ModelErrorCode | Literal["invalid_message", "busy", "rate_limited_local", "spend_cap", "paused"]
)


class ChatError(ContractModel):
    type: Literal["chat.error"] = "chat.error"
    request_id: str | None
    code: ServerErrorCode
    message: str


class Pong(ContractModel):
    type: Literal["pong"] = "pong"


ToolStatus = Literal["ok", "error", "denied", "blocked", "cancelled"]


class ToolStarted(ContractModel):
    type: Literal["tool.started"] = "tool.started"
    request_id: str
    call_id: str
    tool: str
    summary: str
    risk: RiskLevel


class ToolFinished(ContractModel):
    type: Literal["tool.finished"] = "tool.finished"
    request_id: str
    call_id: str
    status: ToolStatus
    preview: str
    duration_ms: float
    image: str | None = None
    """Local-only thumbnail (data URL) of a capture."""
    verified: bool | None = None
    """The tool checked that its action really happened (shown as 'verified')."""
    capture_id: str | None = None
    capture_width: int | None = None
    capture_height: int | None = None
    """Size of the image the model saw; highlight boxes use these pixels."""
    highlight: HighlightBox | None = None


class ApprovalRequested(ContractModel):
    type: Literal["approval.request"] = "approval.request"
    approval_id: str
    request_id: str
    call_id: str
    tool: str
    risk: RiskLevel
    summary: str
    reason: str
    args: dict[str, Any]
    untrusted_context: bool
    can_remember: bool
    expires_in_s: float


class ApprovalResolved(ContractModel):
    type: Literal["approval.resolved"] = "approval.resolved"
    approval_id: str
    approved: bool
    reason: str


class VoicePartial(ContractModel):
    type: Literal["voice.partial"] = "voice.partial"
    stream_id: str
    text: str


class VoiceFinal(ContractModel):
    type: Literal["voice.final"] = "voice.final"
    stream_id: str
    text: str
    mode: VoiceMode
    release_to_final_ms: float


class VoiceError(ContractModel):
    type: Literal["voice.error"] = "voice.error"
    stream_id: str | None
    message: str


class VoiceCommand(ContractModel):
    """From a global shortcut (e.g. a GNOME custom shortcut running the CLI)."""

    type: Literal["voice.command"] = "voice.command"
    action: Literal["toggle_chat", "toggle_dictation", "stop"]


class DictationResult(ContractModel):
    type: Literal["dictation.result"] = "dictation.result"
    stream_id: str
    text: str
    raw: str
    method: Literal["llm", "local", "raw"]
    inserted: bool
    """True when the text went into another app's focused field."""
    note: str = ""


class DownloadProgress(ContractModel):
    type: Literal["download.progress"] = "download.progress"
    model_id: str
    done: int
    total: int
    state: Literal["downloading", "verifying", "installing", "done", "error"]
    error: str = ""


class TaskUpdate(ContractModel):
    type: Literal["task.update"] = "task.update"
    task: TaskInfo


class Notify(ContractModel):
    """Something to tell the user: a reminder, a watcher that fired, a task that finished."""

    type: Literal["notify"] = "notify"
    notify_id: str
    title: str
    body: str
    kind: Literal["reminder", "watcher", "task", "routine", "voice"]
    task_id: str | None = None


class PluginStatus(ContractModel):
    """A plugin started, connected, failed, or paired a new account (the UI reloads it)."""

    type: Literal["plugin.status"] = "plugin.status"
    name: str
    state: ChannelState
    detail: str = ""


class ControlState(ContractModel):
    type: Literal["control.state"] = "control.state"
    paused: bool


ServerMessage = Annotated[
    Hello
    | ChatStarted
    | ChatDelta
    | ChatThinking
    | ChatDone
    | ChatCancelled
    | ChatError
    | Pong
    | ToolStarted
    | ToolFinished
    | ApprovalRequested
    | ApprovalResolved
    | VoicePartial
    | VoiceFinal
    | VoiceError
    | VoiceCommand
    | DictationResult
    | DownloadProgress
    | TaskUpdate
    | Notify
    | PluginStatus
    | ControlState,
    Field(discriminator="type"),
]
server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)
