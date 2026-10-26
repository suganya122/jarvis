"""Tool contract. Tools are registered by core, workers, or plugins and gated by policy."""

from typing import Any, Literal, Protocol, runtime_checkable

from contracts.common import Capability, ContractModel, RiskLevel, Trust
from contracts.model import ImageData

PolicyMode = Literal["allow", "ask", "deny"]


class ToolContext(ContractModel):
    trace_id: str
    session_id: str
    trust: Trust
    """USER unless untrusted content has entered this chat; then every risky call asks."""
    direct: bool = False
    """The user typed this exact command (an instant route), rather than the model choosing it."""


class ToolAssessment(ContractModel):
    """Computed per call, before policy: the real risk can depend on the arguments."""

    risk: RiskLevel
    summary: str
    """One line a person can approve or deny, e.g. 'Overwrite ~/notes.txt (2.1 KB)'."""
    error: str | None = None
    """Set when the call can't run at all (invalid args, path outside allowed folders)."""


class ToolResult(ContractModel):
    ok: bool
    output: str
    """Truncated, model-safe summary of the result."""
    trust: Trust = Trust.USER
    """UNTRUSTED when the output carries outside content (files, web pages, command output)."""
    images: tuple[ImageData, ...] = ()
    """Images for the model (screenshots), already cropped and downscaled."""
    preview: ImageData | None = None
    """Small thumbnail for the local UI only; never sent to the model."""
    evidence: dict[str, Any] = {}
    """Machine-checkable facts used by the verifier and the skill learner."""


class ToolInfo(ContractModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: RiskLevel
    """Baseline risk; assess() may raise it for a specific call."""
    capabilities: tuple[Capability, ...]
    source: str
    """'core', a worker name, or a plugin name."""


@runtime_checkable
class Tool(Protocol):
    info: ToolInfo

    async def assess(self, args: dict[str, Any]) -> ToolAssessment: ...

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...
