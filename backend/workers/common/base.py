"""Base class for worker tools."""

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolInfo, ToolResult


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkerTool[A: Args, E](ABC):
    """A tool implemented inside a worker. E is the worker's environment type."""

    source: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: type[A]
    risk: ClassVar[RiskLevel]
    capabilities: ClassVar[tuple[Capability, ...]]

    def info(self) -> ToolInfo:
        return ToolInfo(
            name=self.name,
            description=self.description,
            input_schema=self.args_model.model_json_schema(),
            risk=self.risk,
            capabilities=self.capabilities,
            source=self.source,
        )

    @abstractmethod
    def assess(self, args: A, env: E) -> ToolAssessment: ...

    @abstractmethod
    async def run(self, args: A, env: E) -> ToolResult: ...


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[truncated {len(text) - limit} characters]"


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
