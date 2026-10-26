"""Background tasks, routines (schedules) and watchers (polled conditions)."""

from typing import Literal

from pydantic import Field

from contracts.common import ContractModel

TaskStatus = Literal["queued", "running", "waiting_approval", "done", "failed", "cancelled", "interrupted"]


class TaskInfo(ContractModel):
    task_id: str
    session_id: str | None
    prompt: str
    origin: str
    """'user', 'routine:<id>' or 'watcher:<id>'."""
    status: TaskStatus
    step: int = 0
    preview: str = ""
    """Last few lines of the reply, for the mini preview."""
    current_tool: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    created_at: float
    finished_at: float | None = None


class Schedule(ContractModel):
    kind: Literal["once", "daily", "weekdays", "weekly", "interval"]
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    """Local HH:MM for daily/weekdays/weekly."""
    days: tuple[int, ...] = ()
    """Weekly: 0 = Monday … 6 = Sunday."""
    every_minutes: int | None = Field(default=None, ge=5, le=7 * 24 * 60)
    at: float | None = None
    """Once: epoch seconds."""


class Routine(ContractModel):
    routine_id: str
    name: str
    prompt: str = Field(min_length=1, max_length=2_000)
    schedule: Schedule
    action: Literal["run", "notify"] = "run"
    """run: do the prompt as a background task. notify: just remind."""
    enabled: bool = True
    created_at: float
    next_run_at: float | None = None
    last_run_at: float | None = None
    last_status: str = ""


WatchKind = Literal["file", "url", "window"]
WatchCondition = Literal["changes", "exists", "gone", "contains", "up", "down"]


class Watcher(ContractModel):
    watcher_id: str
    name: str
    kind: WatchKind
    target: str = Field(min_length=1, max_length=2_000)
    condition: WatchCondition
    text: str = Field(default="", max_length=200)
    every_seconds: int = Field(default=60, ge=15, le=24 * 3600)
    action: Literal["notify", "run"] = "notify"
    prompt: str = Field(default="", max_length=2_000)
    once: bool = True
    status: Literal["active", "paused", "triggered", "error"] = "active"
    created_at: float
    last_checked_at: float | None = None
    last_detail: str = ""
