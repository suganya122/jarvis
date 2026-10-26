"""Model provider contract: one streaming interface for Claude and local servers."""

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from contracts.common import ContractModel


class ModelTier(StrEnum):
    CHEAP = "cheap"
    BALANCED = "balanced"
    MAX = "max"


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ImageData(ContractModel):
    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_b64: str
    width: int
    height: int


IMAGE_OMITTED = "[A screenshot was shown here at the time. Screenshots are not kept.]"


class ToolResultBlock(ContractModel):
    call_id: str
    output: str
    is_error: bool = False
    images: tuple[ImageData, ...] = ()
    """Sent to the model for the current turn only; never persisted."""


class ChatMessage(ContractModel):
    role: Role
    text: str
    provider_state: dict[str, Any] | None = None
    """Opaque provider data replayed verbatim on the next turn (e.g. thinking blocks)."""
    tool_results: tuple[ToolResultBlock, ...] = ()
    """Only on user messages: results for the tool calls of the preceding assistant turn."""
    images: tuple[ImageData, ...] = ()
    """Screen captures the user attached to this message; never persisted."""


class ToolSpec(ContractModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ModelRequest(ContractModel):
    trace_id: str
    system: str
    messages: tuple[ChatMessage, ...]
    tier: ModelTier
    tools: tuple[ToolSpec, ...] = ()
    context: str = ""
    """Per-user context after the stable system prompt (memory). Kept out of `system` so the
    system prompt stays byte-stable for prompt caching."""


class Usage(ContractModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class TextDelta(ContractModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ThinkingStarted(ContractModel):
    type: Literal["thinking_started"] = "thinking_started"


class ToolCallRequested(ContractModel):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    input: dict[str, Any]


class Completed(ContractModel):
    type: Literal["completed"] = "completed"
    provider: str
    model: str
    stop_reason: str
    usage: Usage
    cost_usd: float
    provider_state: dict[str, Any] | None = None


ModelErrorCode = Literal[
    "no_api_key",
    "auth",
    "offline",
    "rate_limited",
    "overloaded",
    "bad_request",
    "provider_error",
    "spend_cap",
]


class ModelError(ContractModel):
    type: Literal["error"] = "error"
    code: ModelErrorCode
    message: str
    retryable: bool = False


ModelEvent = Annotated[
    TextDelta | ThinkingStarted | ToolCallRequested | Completed | ModelError,
    Field(discriminator="type"),
]
"""A stream always ends with exactly one Completed or ModelError event."""


class ProviderStatus(ContractModel):
    provider: str
    ready: bool
    detail: str = ""


@runtime_checkable
class ModelProvider(Protocol):
    @property
    def name(self) -> str: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Stream events for one model turn. Cancelling the consumer aborts the request."""
        ...

    async def status(self) -> ProviderStatus: ...
