"""Tools for memory. Saving or deleting needs the `memory` capability (asks by default), so
content the model read can't plant 'facts' that steer later chats."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contracts.common import Capability, RiskLevel
from contracts.memory import MAX_MEMORY_CHARS
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.memory import MemoryStore, tidy


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RememberArgs(_Args):
    text: str = Field(
        min_length=2, max_length=MAX_MEMORY_CHARS, description="The fact, as one short sentence."
    )


class QueryArgs(_Args):
    query: str = Field(default="", max_length=200, description="Words the memory contains; empty for all.")


def _parse[M: BaseModel](model: type[M], args: dict[str, Any]) -> M | str:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        return "invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:3])


class Remember:
    info = ToolInfo(
        name="remember",
        description="Save a fact the user wants you to remember in later chats (preferences, names, dates).",
        input_schema=RememberArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(Capability.MEMORY,),
        source="core",
    )

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(RememberArgs, args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Remember something", error=a)
        return ToolAssessment(risk=RiskLevel.WRITE, summary=f"Remember: “{tidy(a.text)}”")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(RememberArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        source = "user" if ctx.direct else "assistant"
        memory = await self._store.add(a.text, source)
        return ToolResult(
            ok=True, output=f"Got it. I'll remember: {memory.text}", evidence={"memory_id": memory.memory_id}
        )


class Forget:
    info = ToolInfo(
        name="forget",
        description="Delete one saved memory that contains the given words.",
        input_schema=QueryArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(Capability.MEMORY,),
        source="core",
    )

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def _match(self, args: dict[str, Any]) -> tuple[str, str | None]:
        """(memory_id or "", error)."""
        a = _parse(QueryArgs, args)
        if isinstance(a, str):
            return "", a
        if not a.query.strip():
            return "", "say what to forget"
        found = await self._store.find(a.query)
        if not found:
            return "", f"nothing saved mentions “{a.query}”"
        if len(found) > 1:
            listed = "; ".join(m.text for m in found[:5])
            return "", f"{len(found)} memories match ({listed}). Be more specific, or use the Memory panel."
        return found[0].memory_id, None

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        memory_id, error = await self._match(args)
        memory = await self._store.get(memory_id) if memory_id else None
        summary = f"Forget: “{memory.text}”" if memory else "Forget something"
        return ToolAssessment(risk=RiskLevel.WRITE, summary=summary, error=error)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        memory_id, error = await self._match(args)
        if error:
            return ToolResult(ok=False, output=error)
        memory = await self._store.get(memory_id)
        await self._store.delete(memory_id)
        return ToolResult(ok=True, output=f"Forgotten: {memory.text if memory else memory_id}")


class Recall:
    info = ToolInfo(
        name="recall_memories",
        description="List what the user asked you to remember, optionally only memories with some words.",
        input_schema=QueryArgs.model_json_schema(),
        risk=RiskLevel.READ,
        capabilities=(),
        source="core",
    )

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(QueryArgs, args)
        error = a if isinstance(a, str) else None
        return ToolAssessment(risk=RiskLevel.READ, summary="Look through saved memories", error=error)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(QueryArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        memories = await self._store.find(a.query) if a.query.strip() else await self._store.all()
        if not memories:
            return ToolResult(ok=True, output="Nothing saved yet." if not a.query else "Nothing about that.")
        return ToolResult(ok=True, output="\n".join(f"- {m.text}" for m in memories))
