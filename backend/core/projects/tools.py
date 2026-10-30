"""The model's tools for projects: save what it has understood, and look a project up."""

from collections.abc import Callable
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contracts.common import RiskLevel
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.projects.store import WATCHED, Project, ProjectStore, Status


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaveArgs(_Args):
    name: str = Field(min_length=2, max_length=80, description="Short name, e.g. 'Bakery website'.")
    idea: str = Field(default="", max_length=2_000, description="The idea in the user's words (first save).")
    learned: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list,
        max_length=20,
        description="New things understood: answers, decisions, features, preferences (one each).",
    )
    brief: str = Field(default="", max_length=50_000, description="The full prompt, once written.")
    built_with: str = Field(
        default="", max_length=80, description="Where: 'Claude', 'Claude Code', 'ChatGPT'…"
    )
    status: Status | None = Field(
        default=None,
        description=(
            "understanding (asking), ready (brief written), sent (handed to the other AI: watched), "
            "answered, paused, done (stops being tracked)."
        ),
    )
    watch_window: str = Field(
        default="", max_length=120, description="When sent: part of that AI's window title, e.g. 'Claude'."
    )
    working_marker: str = Field(
        default="",
        max_length=60,
        description="When sent: a control seen only while it works, e.g. 'Stop' (in its window).",
    )
    detail: str = Field(default="", max_length=300, description="Where it stands / what's next.")


class FindArgs(_Args):
    name: str = Field(default="", max_length=80, description="The project; empty for all of them.")


def _parse[M: BaseModel](model: type[M], args: dict[str, Any]) -> M | str:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        return "invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:3])


def describe(p: Project, brief: bool = False) -> str:
    """Where it stands first (that must never be cut off), then what it is and what was understood."""
    parts = [f"{p.name} [{p.status}{' with ' + p.built_with if p.built_with else ''}]"]
    if p.detail:
        parts.append(f"now: {p.detail}")
    if p.idea:
        parts.append(f"idea: {p.idea if brief else p.idea[:300]}")
    if p.learned:
        parts.append("understood: " + "; ".join(p.learned if brief else [x[:200] for x in p.learned]))
    if p.brief:
        parts.append(f"brief:\n{p.brief}" if brief else f"brief written ({len(p.brief)} characters)")
    return " — ".join(parts)


class SaveProject:
    info = ToolInfo(
        name="save_project",
        description=(
            "Keep track of something the user is building or working out with another AI (Claude, "
            "Claude Code, ChatGPT…): create it, add what you understood as you ask, store the brief, and "
            "mark it sent (with its window) so it is watched in the background and you're told when it "
            "answers. Saved projects stay in your context until they're done."
        ),
        input_schema=SaveArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(),
        source="core",
    )

    def __init__(self, store: ProjectStore, on_sent: Callable[[], None]) -> None:
        self._store = store
        self._on_sent = on_sent

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(SaveArgs, args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Save a project", error=a)
        return ToolAssessment(risk=RiskLevel.WRITE, summary=f"Keep track of “{a.name}”")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(SaveArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        project = await self._store.named(a.name) or await self._store.add(a.name, a.idea)
        if a.idea and not project.idea:
            project.idea = a.idea
        known = {x.casefold() for x in project.learned}
        for item in (x.strip() for x in a.learned):
            if item and item.casefold() not in known:
                known.add(item.casefold())
                project.learned.append(item)
        for key in ("brief", "built_with", "watch_window", "working_marker", "detail"):
            if value := getattr(a, key):
                setattr(project, key, value)
        if a.status in WATCHED and not project.watch_window:
            return ToolResult(
                ok=False, output="Say which window to watch (part of its title), then save it as sent."
            )
        await self._store.save(project)
        if a.status is not None:  # a new step: any check the watcher had queued is superseded
            await self._store.update(project, status=a.status, review_due=False)
        if project.status in WATCHED:
            self._on_sent()
            return ToolResult(
                ok=True, output=f"Saved. Watching {project.name}; I'll tell the user when it answers."
            )
        return ToolResult(ok=True, output=f"Saved: {describe(project)}")


class ProjectStatus:
    info = ToolInfo(
        name="project_status",
        description=(
            "Look up a project (or all of them): what it is, what was understood, its brief, where it stands."
        ),
        input_schema=FindArgs.model_json_schema(),
        risk=RiskLevel.READ,
        capabilities=(),
        source="core",
    )

    def __init__(self, store: ProjectStore) -> None:
        self._store = store

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="Look up a project")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(FindArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        if a.name:
            project = await self._store.find(a.name)
            if project is None:
                return ToolResult(ok=False, output=f"No project called “{a.name}”.")
            return ToolResult(ok=True, output=describe(project, brief=True))
        projects = await self._store.all()
        if not projects:
            return ToolResult(ok=True, output="No projects yet.")
        return ToolResult(ok=True, output="\n".join(f"- {describe(p)}" for p in projects[:20]))
