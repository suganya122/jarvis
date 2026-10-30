"""Tool registry plus the few tools that need no OS access and live in core."""

from datetime import datetime
from typing import Any

from contracts.common import RiskLevel
from contracts.model import ToolSpec
from contracts.tools import Tool, ToolAssessment, ToolContext, ToolInfo, ToolResult

INTERNAL_TOOLS = frozenset({"desktop_notify", "active_context"})
"""Used by core itself (reminders, watchers); never offered to or callable by the model."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.info.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.info.name}")
        self._tools[tool.info.name] = tool

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def remove_source(self, source: str) -> None:
        self._tools = {n: t for n, t in self._tools.items() if t.info.source != source}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def infos(self) -> list[ToolInfo]:
        return [self._tools[n].info for n in sorted(self._tools)]

    def specs(self) -> tuple[ToolSpec, ...]:
        # Sorted, so the tool list is byte-stable and stays in the prompt cache.
        return tuple(
            ToolSpec(name=i.name, description=i.description, input_schema=i.input_schema)
            for i in self.infos()
            if i.name not in INTERNAL_TOOLS
        )


class CurrentTime:
    info = ToolInfo(
        name="current_time",
        description="Get the current local date, time, and time zone.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=RiskLevel.READ,
        capabilities=(),
        source="core",
    )

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="Check the time")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        now = datetime.now().astimezone()
        return ToolResult(ok=True, output=f"{now:%A %Y-%m-%d %H:%M:%S} {now.tzname()} (UTC{now:%z})")
