"""The tools: build an ability, list and remove them, and one tool per ability built."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contracts.common import Capability, RiskLevel, Trust
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.abilities import runner
from core.abilities.builder import Builder
from core.abilities.spec import AbilitySpec

PREFIX = "ability_"
OUTSIDE = frozenset({Capability.NET_FETCH, Capability.FS_READ, Capability.SCREEN_READ})
"""What it returns from these is outside content: data, never instructions."""


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildArgs(_Args):
    goal: str = Field(
        min_length=5, max_length=600, description="What the new ability should do, in the user's terms."
    )
    details: str = Field(
        default="", max_length=2_000, description="Anything that helps: apps, sites, formats, what failed."
    )


class NameArgs(_Args):
    name: str = Field(min_length=3, max_length=60, description="The ability's name (as listed).")


def _parse[M: BaseModel](model: type[M], args: dict[str, Any]) -> M | str:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        return "invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:3])


class AbilityTool:
    """One built ability, run in its own process."""

    def __init__(self, spec: AbilitySpec, folder: Path) -> None:
        self.spec = spec
        self._folder = folder
        self.info = ToolInfo(
            name=PREFIX + spec.name,
            description=f"{spec.description} (An ability you built.)",
            input_schema=spec.input_schema,
            risk=spec.risk,
            capabilities=(Capability.SHELL_RUN, *spec.capabilities),
            source="ability",
        )

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(
            risk=self.spec.risk, summary=f"Use my {self.spec.name.replace('_', ' ')} ability"
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ran = await runner.run(self._folder, args)
        trust = Trust.UNTRUSTED if OUTSIDE & set(self.spec.capabilities) else Trust.USER
        return ToolResult(ok=ran.ok, output=ran.output or ("Done." if ran.ok else "It failed."), trust=trust)


class BuildAbility:
    info = ToolInfo(
        name="build_ability",
        description=(
            "When no tool can do what the user asked, build a new ability for yourself: the most "
            "capable model writes a small program for it, which is installed and tested on this "
            "computer and then becomes a tool you can use from now on. The user is always asked first."
        ),
        input_schema=BuildArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(Capability.SHELL_RUN,),
        source="core",
    )

    def __init__(
        self, builder: Builder, taken: Callable[[], set[str]], add: Callable[[AbilitySpec], str]
    ) -> None:
        self._builder = builder
        self._taken = taken
        self._add = add

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(BuildArgs, args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Build a new ability", error=a)
        return ToolAssessment(
            risk=RiskLevel.WRITE,
            summary=f"Build myself a new ability (write and run new code on this computer): {a.goal}",
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(BuildArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        built = await self._builder.build(a.goal, a.details, self._taken())
        if built.spec is None:
            return ToolResult(ok=False, output=f"Couldn't build it: {built.message}")
        name = self._add(built.spec)
        return ToolResult(
            ok=True,
            output=f"Built and tested. It is now the tool {name}: {built.spec.description}\n"
            f"Test run said: {built.message[:500]}\nUse it now to do what the user asked.",
            evidence={"ability": built.spec.name},
        )


class ListAbilities:
    info = ToolInfo(
        name="list_abilities",
        description="List the abilities you have built for yourself.",
        input_schema=_Args.model_json_schema(),
        risk=RiskLevel.READ,
        capabilities=(),
        source="core",
    )

    def __init__(self, specs: Callable[[], list[AbilitySpec]]) -> None:
        self._specs = specs

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="List my abilities")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        specs = self._specs()
        if not specs:
            return ToolResult(ok=True, output="No abilities built yet.")
        return ToolResult(ok=True, output="\n".join(f"- {s.name}: {s.description}" for s in specs))


class RemoveAbility:
    info = ToolInfo(
        name="remove_ability",
        description="Delete an ability you built (its code and what was installed for it).",
        input_schema=NameArgs.model_json_schema(),
        risk=RiskLevel.DESTRUCTIVE,
        capabilities=(Capability.FS_WRITE,),
        source="core",
    )

    def __init__(self, remove: Callable[[str], bool]) -> None:
        self._remove = remove

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(NameArgs, args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.DESTRUCTIVE, summary="Delete an ability", error=a)
        return ToolAssessment(risk=RiskLevel.DESTRUCTIVE, summary=f"Delete my {a.name} ability")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(NameArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        name = a.name.removeprefix(PREFIX)
        done = self._remove(name)
        return ToolResult(ok=done, output=f"Deleted {name}." if done else f"No ability called {name}.")
