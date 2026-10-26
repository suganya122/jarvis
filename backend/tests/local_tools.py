"""Run a worker tool in-process (the real worker runs the same code over a pipe)."""

from typing import Any

from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult


class LocalTool:
    """A worker tool with its environment, as a core Tool."""

    def __init__(self, tool: Any, env: Any) -> None:
        self._tool, self._env = tool, env
        self.info: ToolInfo = tool.info()

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return self._tool.assess(self._tool.args_model.model_validate(args), self._env)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        result: ToolResult = await self._tool.run(self._tool.args_model.model_validate(args), self._env)
        return result
