"""Per-turn state for the chat pipeline."""

from dataclasses import dataclass, field

from contracts.model import ChatMessage, Completed, ToolCallRequested, ToolResultBlock, Usage
from contracts.skills import TraceStep


@dataclass
class Step:
    assistant: ChatMessage
    calls: list[ToolCallRequested]
    results: dict[str, ToolResultBlock] = field(default_factory=dict)


@dataclass
class Turn:
    trace_id: str
    started: float
    session_id: str | None = None
    parts: list[str] = field(default_factory=list)
    pending: Step | None = None
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    model: str = ""
    first_token_ms: float | None = None
    trace: list[TraceStep] = field(default_factory=list)
    tainted_before: bool = False
    speculation: str = ""
    background: bool = False
    escalated: str | None = None
    """Why this turn moved up a tier ('a step failed', 'multi-step task')."""
    """'used' or 'discarded' when a voice speculation was offered to this turn."""

    @property
    def total_tokens(self) -> int:
        u = self.usage
        return u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens

    def add_usage(self, done: Completed) -> None:
        a, b = self.usage, done.usage
        self.usage = Usage(
            input_tokens=a.input_tokens + b.input_tokens,
            output_tokens=a.output_tokens + b.output_tokens,
            cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
            cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
        )
        self.cost_usd += done.cost_usd
        self.model = done.model
