"""Tools that create routines and watchers. They need the `schedule` capability (asks by default),
so a web page or file can't quietly set up something that runs later."""

import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contracts.automation import Schedule, WatchCondition, Watcher, WatchKind
from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.schedule import next_run, parse_once, parse_recurring, sentence

if TYPE_CHECKING:  # avoids an import cycle
    from core.scheduler import Automations

CONDITIONS: dict[str, set[str]] = {
    "url": {"up", "down", "contains", "changes"},
    "file": {"changes", "exists", "gone"},
    "window": {"exists", "gone"},
}


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutineArgs(_Args):
    prompt: str = Field(min_length=1, max_length=2_000, description="What to do (or what to remind about).")
    when: str = Field(
        max_length=100, description="e.g. 'every weekday at 8am', 'every 2 hours', 'at 5pm', 'in 20 minutes'."
    )
    action: Literal["run", "notify"] = Field(
        default="run", description="run: do it then. notify: just remind me."
    )


def parse_when(when: str, now: datetime) -> Schedule | None:
    return parse_recurring(when) or parse_once(when, now)


class CreateRoutine:
    info = ToolInfo(
        name="create_routine",
        description="Schedule something: a reminder, or a task to do at a time or on a repeating schedule.",
        input_schema=RoutineArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(Capability.SCHEDULE,),
        source="core",
    )

    def __init__(self, automations: Callable[[], "Automations"], now: Callable[[], datetime]) -> None:
        self._automations = automations
        self._now = now

    def _parse(self, args: dict[str, Any]) -> tuple[RoutineArgs, Schedule] | str:
        try:
            parsed = RoutineArgs.model_validate(args)
        except ValidationError as exc:
            return "invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:3])
        schedule = parse_when(parsed.when, self._now())
        if schedule is None or next_run(schedule, self._now()) is None:
            return f"I couldn't understand when “{parsed.when}” is (try 'every weekday at 8am' or 'at 5pm')"
        return parsed, schedule

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        parsed = self._parse(args)
        if isinstance(parsed, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Schedule something", error=parsed)
        a, schedule = parsed
        verb = "remind you" if a.action == "notify" else "run"
        return ToolAssessment(risk=RiskLevel.WRITE, summary=f"{sentence(schedule)}, {verb}: “{a.prompt}”")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        parsed = self._parse(args)
        if isinstance(parsed, str):
            return ToolResult(ok=False, output=parsed)
        a, schedule = parsed
        name = a.prompt if len(a.prompt) <= 60 else a.prompt[:57] + "…"
        routine = await self._automations().add_routine(name, a.prompt, schedule, a.action)
        nxt = datetime.fromtimestamp(routine.next_run_at).astimezone() if routine.next_run_at else None
        when = f" Next: {nxt:%a %d %b at %H:%M}." if nxt else ""
        verb = "remind you" if a.action == "notify" else "do it"
        return ToolResult(
            ok=True,
            output=f"Saved. {sentence(schedule)}, I'll {verb}.{when}",
            evidence={"routine_id": routine.routine_id},
        )


class WatcherArgs(_Args):
    kind: WatchKind
    target: str = Field(min_length=1, max_length=2_000, description="A URL, a file path, or a window title.")
    condition: WatchCondition
    text: str = Field(default="", max_length=200, description="For 'contains': the text to look for.")
    every_seconds: int = Field(default=60, ge=15, le=86_400)
    action: Literal["notify", "run"] = "notify"
    prompt: str = Field(
        default="", max_length=2_000, description="For action 'run': what to do when it happens."
    )


def watcher_name(a: WatcherArgs) -> str:
    target = urlparse(a.target).netloc or a.target if a.kind == "url" else a.target
    phrase = {
        "up": "is up",
        "down": "is down",
        "contains": f"shows “{a.text}”",
        "changes": "changes",
        "exists": "appears" if a.kind == "window" else "exists",
        "gone": "is gone",
    }[a.condition]
    noun = f"a window called “{target}”" if a.kind == "window" else target
    return f"When {noun} {phrase}"


class CreateWatcher:
    info = ToolInfo(
        name="create_watcher",
        description=(
            "Watch for something and tell the user when it happens: a website is up/down or shows "
            "some text, a file changes/appears/disappears, or a window with some title opens or closes."
        ),
        input_schema=WatcherArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(Capability.SCHEDULE,),
        source="core",
    )

    def __init__(self, automations: Callable[[], "Automations"]) -> None:
        self._automations = automations

    def _parse(self, args: dict[str, Any]) -> WatcherArgs | str:
        try:
            a = WatcherArgs.model_validate(args)
        except ValidationError as exc:
            return "invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:3])
        if a.condition not in CONDITIONS[a.kind]:
            return f"a {a.kind} watcher can check: {', '.join(sorted(CONDITIONS[a.kind]))}"
        if a.condition == "contains" and not a.text:
            return "say what text to look for"
        if a.action == "run" and not a.prompt:
            return "say what to do when it happens"
        if a.kind == "url":
            url = a.target if "://" in a.target else f"https://{a.target}"
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return "only http(s) addresses can be watched"
            a = a.model_copy(update={"target": url})
        return a

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = self._parse(args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Watch for something", error=a)
        return ToolAssessment(
            risk=RiskLevel.WRITE, summary=f"{watcher_name(a)}, tell you (checked every {a.every_seconds} s)"
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = self._parse(args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        watcher = Watcher(
            watcher_id=uuid.uuid4().hex[:12], name=watcher_name(a), created_at=time.time(), **a.model_dump()
        )
        await self._automations().add_watcher(watcher)
        return ToolResult(
            ok=True,
            output=f"Watching. {watcher.name}, I'll tell you.",
            evidence={"watcher_id": watcher.watcher_id},
        )
