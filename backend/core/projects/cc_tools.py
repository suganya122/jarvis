"""The model's tools for Claude Code: start it on a project's brief, and send it the next message
(fixes after a check). Both run it in the project's folder; see core.projects.claude_code."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import Field

from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.projects import claude_code
from core.projects.store import Project, ProjectStore
from core.projects.tools import _Args, _parse

SHOWN_HOME = str(Path.home())


class StartArgs(_Args):
    project: str = Field(min_length=2, max_length=80, description="The saved project, with its brief.")
    folder: str = Field(
        default="", max_length=60, description="Folder name in ~/Projects (default: from the name)."
    )


class TellArgs(_Args):
    project: str = Field(min_length=2, max_length=80)
    message: str = Field(min_length=1, max_length=20_000, description="What to fix or do next, precisely.")


def _shown(folder: Path) -> str:
    return str(folder).replace(SHOWN_HOME, "~", 1)


class _ClaudeCodeTool:
    def __init__(
        self, store: ProjectStore, protected: Callable[[], list[str]], on_sent: Callable[[], None]
    ) -> None:
        self._store = store
        self._protected = protected
        self._on_sent = on_sent

    async def _project(self, name: str) -> Project | str:
        project = await self._store.named(name) or await self._store.find(name)
        return (
            project if project is not None else f"No project called “{name}”: save it first (save_project)."
        )

    async def _working(self, project: Project, folder: Path, detail: str) -> None:
        await self._store.update(
            project, folder=str(folder), status="working", detail=detail, review_due=False
        )
        self._on_sent()


class StartClaudeCode(_ClaudeCodeTool):
    info = ToolInfo(
        name="start_claude_code",
        description=(
            "Hand a saved project's brief to Claude Code: it builds it in ~/Projects/<folder>, run by you "
            "in the background (never type it into Terminal), with a Terminal window showing its progress. "
            "It is watched from then on; when it finishes you check the work."
        ),
        input_schema=StartArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(Capability.SHELL_RUN,),
        source="core",
    )

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(StartArgs, args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Start Claude Code", error=a)
        folder = claude_code.folder_for(a.folder or a.project)
        return ToolAssessment(
            risk=RiskLevel.WRITE, summary=f"Start Claude Code on “{a.project}” in {_shown(folder)}"
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(StartArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        project = await self._project(a.project)
        if isinstance(project, str):
            return ToolResult(ok=False, output=project)
        if not project.brief:
            return ToolResult(
                ok=False, output="Write the brief first and save it in the project (save_project)."
            )
        folder = (
            Path(project.folder)
            if project.folder and not a.folder
            else claude_code.folder_for(a.folder or project.name)
        )
        claude_code.prepare(folder, self._protected())
        why_not = await claude_code.launch(folder, project.brief + claude_code.FINISH, carry_on=False)
        if why_not:
            return ToolResult(ok=False, output=why_not)
        if project.built_with != "Claude Code":
            project.built_with = "Claude Code"
            await self._store.save(project)
        await self._working(project, folder, f"Claude Code is building it in {_shown(folder)}.")
        return ToolResult(
            ok=True,
            output=f"Claude Code is building {project.name} in {_shown(folder)}; a Terminal window shows its "
            "progress. It's watched: when it finishes, you check the work.",
        )


class TellClaudeCode(_ClaudeCodeTool):
    info = ToolInfo(
        name="tell_claude_code",
        description=(
            "Send Claude Code the next message on a project it is building (what to fix, the user's answer): "
            "it carries on in the same conversation and folder, and is watched again."
        ),
        input_schema=TellArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(Capability.SHELL_RUN,),
        source="core",
    )

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(TellArgs, args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Message Claude Code", error=a)
        return ToolAssessment(
            risk=RiskLevel.WRITE, summary=f"Tell Claude Code what to do next on “{a.project}”"
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(TellArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        project = await self._project(a.project)
        if isinstance(project, str):
            return ToolResult(ok=False, output=project)
        if not project.folder:
            return ToolResult(
                ok=False, output="Claude Code isn't building this one yet: use start_claude_code."
            )
        folder = Path(project.folder)
        claude_code.prepare(folder, self._protected())
        why_not = await claude_code.launch(folder, a.message + claude_code.FINISH, carry_on=True, show=False)
        if why_not:
            return ToolResult(ok=False, output=why_not)
        await self._working(project, folder, "Claude Code is working on the fixes.")
        return ToolResult(
            ok=True, output=f"Sent. Claude Code is working on it in {_shown(folder)}; it's watched."
        )
