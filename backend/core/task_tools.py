"""The conversation's tools for what's running (core.tasks): look at it, and add to, pause, resume or
cancel it, so a new message never has to wait for a request to finish or start it over."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contracts.common import RiskLevel
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.tasks import Task, TaskBoard


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LookArgs(_Args):
    task: str = Field(default="", max_length=200, description="Which: 't2', or words from it. Empty: all.")


class ManageArgs(_Args):
    task: str = Field(
        min_length=1, max_length=200, description="Which: 't2', or words from it ('the EB bill')."
    )
    action: Literal["add", "pause", "resume", "cancel"] = Field(
        description="add: pass on what the user added or changed (it reads it before its next step)."
    )
    message: str = Field(default="", max_length=2_000, description="For add: what the user said, in full.")


def _parse[M: BaseModel](model: type[M], args: dict[str, Any]) -> M | str:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        return "invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:3])


def _detail(task: Task, now: float) -> str:
    steps = "\n".join(f"  {i}. {s}" for i, s in enumerate(task.steps, start=task.count - len(task.steps) + 1))
    said = task.said.strip()
    return "\n".join(
        part
        for part in (
            task.line(now),
            f"Asked: “{task.text}”",
            f"Its last steps:\n{steps}" if steps else "No steps yet.",
            f"What it said lately: “{said[-600:]}”" if said else "",
        )
        if part
    )


class LookAtTasks:
    info = ToolInfo(
        name="tasks",
        description=(
            "What you're working on besides this request: each one's state, steps so far and what it's "
            "doing now, from the steps themselves. Use it to answer how something is going."
        ),
        input_schema=LookArgs.model_json_schema(),
        risk=RiskLevel.READ,
        capabilities=(),
        source="core",
    )

    def __init__(self, board: TaskBoard) -> None:
        self._board = board

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="Check what I'm working on")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(LookArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        me = self._board.by_trace(ctx.trace_id)
        but, now = me.request_id if me else "", self._board.now()
        if a.task:
            task = self._board.find(a.task, but)
            if task is None:
                return ToolResult(ok=False, output=f"Nothing matches “{a.task}”. {self._list(but, now)}")
            return ToolResult(ok=True, output=_detail(task, now))
        return ToolResult(ok=True, output=self._list(but, now))

    def _list(self, but: str, now: float) -> str:
        tasks = self._board.all(but)
        if not tasks:
            return "Nothing else is running, and nothing finished lately."
        return "\n".join(t.line(now) for t in tasks)


class ManageTask:
    info = ToolInfo(
        name="manage_task",
        description=(
            "Change a request that's already running: add what the user added or changed (it carries on "
            "with that), pause it, resume it, or cancel it. Never start the same work again instead."
        ),
        input_schema=ManageArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(),
        source="core",
    )

    def __init__(self, board: TaskBoard) -> None:
        self._board = board

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(ManageArgs, args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Change a running request", error=a)
        task = self._board.find(a.task)
        what = f"{task.ref} (“{task.text[:60]}”)" if task else f"“{a.task}”"
        verb = {"add": "Tell", "pause": "Pause", "resume": "Resume", "cancel": "Cancel"}[a.action]
        return ToolAssessment(risk=RiskLevel.WRITE, summary=f"{verb} {what}")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(ManageArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        me = self._board.by_trace(ctx.trace_id)
        task = self._board.find(a.task, me.request_id if me else "")
        if task is None:
            return ToolResult(ok=False, output=f"Nothing running matches “{a.task}”: check with tasks.")
        if not task.live:
            return ToolResult(ok=False, output=f"{task.ref} has already finished ({task.state}).")
        if a.action == "add":
            if not a.message.strip():
                return ToolResult(ok=False, output="Say what to pass on in message.")
            self._board.add(task, a.message.strip())
            when = "when it's resumed" if task.state == "paused" else "before its next step"
            return ToolResult(ok=True, output=f"Passed on to {task.ref}; it reads it {when}.")
        if a.action == "pause":
            self._board.pause(task)
            return ToolResult(ok=True, output=f"{task.ref} pauses after the step it's on, until resumed.")
        if a.action == "resume":
            self._board.resume(task)
            return ToolResult(ok=True, output=f"{task.ref} carries on.")
        if not self._board.cancel(task):
            return ToolResult(ok=False, output=f"{task.ref} can't be cancelled now.")
        return ToolResult(ok=True, output=f"{task.ref} is cancelled.")
