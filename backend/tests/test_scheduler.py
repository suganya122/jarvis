"""Routines and watchers: due runs, missed runs, manual runs, polling with zero model calls."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite
import httpx
import pytest

from contracts.automation import Schedule, TaskInfo, Watcher
from contracts.common import Capability, RiskLevel
from contracts.tools import ToolContext, ToolResult
from contracts.ws import Notify, ServerMessage
from core import db
from core.automation_store import AutomationStore
from core.config import Settings
from core.notify import Notifier
from core.redact import Redactor
from core.scheduler import MISSED_GRACE_S, Automations
from core.store import Store
from core.toolbox import ToolRegistry
from core.watchers import WatcherService
from tests.tools_fixture import FakeTool

TZ = ZoneInfo("Europe/Berlin")


class FakeBackground:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, str]] = []

    async def submit(self, prompt: str, *, origin: str = "user", **_: Any) -> TaskInfo:
        self.submitted.append((prompt, origin))
        return TaskInfo(task_id="t" * 16, session_id=None, prompt=prompt, origin=origin, status="queued",
                        created_at=0)  # fmt: skip


class FileInfoTool(FakeTool):
    def __init__(self) -> None:
        super().__init__("file_info", RiskLevel.READ, (Capability.FS_READ,))
        self.evidence: dict[str, Any] = {"exists": False, "mtime": None, "size": None}

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.runs.append(args)
        return ToolResult(ok=True, output="", evidence=dict(self.evidence))


class Rig:
    def __init__(self, conn: Any, settings: Settings, transport: httpx.MockTransport | None) -> None:
        self.clock = datetime(2026, 10, 23, 7, 59, tzinfo=TZ)  # a Friday
        self.sent: list[ServerMessage] = []
        self.settings = settings
        self.registry = ToolRegistry()
        self.desktop = FakeTool("desktop_notify", RiskLevel.READ, ())
        self.files = FileInfoTool()
        self.registry.register(self.desktop)
        self.registry.register(self.files)
        self.store = AutomationStore(conn)
        self.notifier = Notifier(
            self.broadcast, self.registry, lambda: self.settings, Store(conn, Redactor())
        )
        self.background = FakeBackground()
        watchers = WatcherService(self.registry, self.notifier, lambda: self.settings, transport)
        self.auto = Automations(self.store, self.background, self.notifier, watchers,  # type: ignore[arg-type]
                                now=lambda: self.clock)  # fmt: skip

    async def broadcast(self, message: ServerMessage) -> None:
        self.sent.append(message)

    @property
    def notes(self) -> list[Notify]:
        return [m for m in self.sent if isinstance(m, Notify)]


@pytest.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    connection = await db.connect(tmp_path / "t.sqlite3")
    yield connection
    await connection.close()


@pytest.fixture
def rig(conn: aiosqlite.Connection) -> Rig:
    return Rig(conn, Settings(), None)


async def test_a_due_routine_starts_a_background_task_and_reschedules(rig: Rig) -> None:
    routine = await rig.auto.add_routine(
        "build", "check the build", Schedule(kind="weekdays", time="08:00"), "run"
    )
    await rig.auto.tick()
    assert rig.background.submitted == []  # 07:59: not yet
    rig.clock += timedelta(minutes=1)
    await rig.auto.tick()
    assert rig.background.submitted == [("check the build", f"routine:{routine.routine_id}")]
    [saved] = await rig.store.routines()
    assert saved.last_status.startswith("started task")
    assert saved.next_run_at == datetime(2026, 10, 26, 8, 0, tzinfo=TZ).timestamp()  # Monday
    await rig.auto.tick()
    assert len(rig.background.submitted) == 1  # ran once only


async def test_a_reminder_notifies_without_any_model_and_once_disables_itself(rig: Rig) -> None:
    at = (rig.clock + timedelta(minutes=5)).timestamp()
    await rig.auto.add_routine("stretch", "stretch", Schedule(kind="once", at=at), "notify")
    rig.clock += timedelta(minutes=5)
    await rig.auto.tick()
    assert [(n.title, n.kind) for n in rig.notes] == [("stretch", "reminder")]
    assert rig.desktop.runs == [{"title": "stretch", "body": "stretch"}]
    [saved] = await rig.store.routines()
    assert not saved.enabled and saved.next_run_at is None
    assert rig.background.submitted == []


async def test_desktop_notifications_can_be_turned_off(rig: Rig) -> None:
    bg = rig.settings.background.model_copy(update={"desktop_notifications": False})
    rig.settings = rig.settings.model_copy(update={"background": bg})
    await rig.notifier.notify("hi", "there", "task")
    assert rig.notes and rig.desktop.runs == []


async def test_missed_runs_are_reported_never_run_late(rig: Rig) -> None:
    await rig.auto.add_routine("report", "send report", Schedule(kind="daily", time="08:00"), "run")
    rig.clock += timedelta(seconds=MISSED_GRACE_S + 3600)  # the app was off at 08:00
    await rig.auto.catch_up()
    assert rig.background.submitted == []
    assert [n.title for n in rig.notes] == ["Missed: report"]
    [saved] = await rig.store.routines()
    assert saved.last_status.startswith("missed") and saved.enabled
    assert saved.next_run_at == datetime(2026, 10, 24, 8, 0, tzinfo=TZ).timestamp()


async def test_manual_run_keeps_the_schedule(rig: Rig) -> None:
    routine = await rig.auto.add_routine("r", "do it", Schedule(kind="daily", time="08:00"), "run")
    await rig.auto.run_routine(routine, manual=True)
    [saved] = await rig.store.routines()
    assert saved.next_run_at == routine.next_run_at and saved.last_run_at is not None
    assert len(rig.background.submitted) == 1


async def test_pause_holds_automations(conn: aiosqlite.Connection) -> None:
    rig = Rig(conn, Settings(), None)
    rig.auto._paused = lambda: True
    await rig.auto.add_routine("r", "do it", Schedule(kind="interval", every_minutes=5), "run")
    rig.clock += timedelta(hours=1)
    await rig.auto.tick()
    assert rig.background.submitted == []


# ---- watchers ----------------------------------------------------------------------------


def make_watcher(kind: str, condition: str, target: str, **extra: Any) -> Watcher:
    return Watcher(watcher_id=f"{kind}-{condition}", name=f"When {target}", kind=kind,  # type: ignore[arg-type]
                   target=target, condition=condition, created_at=0, **extra)  # fmt: skip


async def poll_now(rig: Rig) -> None:
    """Poll every active watcher regardless of its interval."""
    for w, _ in await rig.store.watchers():
        await rig.store.save_watcher(w.model_copy(update={"last_checked_at": None}))
    await rig.auto.poll_watchers()


async def test_url_watcher_fires_once_and_keeps_no_page_text(conn: aiosqlite.Connection) -> None:
    pages = iter(["Sold out", "Sold out", "Back in stock!"])
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, text=next(pages))

    rig = Rig(conn, Settings(), httpx.MockTransport(handler))
    await rig.auto.add_watcher(
        make_watcher("url", "contains", "https://shop.test/x", text="in stock", once=True)
    )
    await poll_now(rig)
    await poll_now(rig)
    assert rig.notes == []
    await poll_now(rig)
    assert [n.kind for n in rig.notes] == ["watcher"] and "in stock" in rig.notes[0].body
    [(saved, state)] = await rig.store.watchers()
    assert saved.status == "triggered" and "body" not in state
    await poll_now(rig)  # triggered watchers stop polling
    assert len(hits) == 3


async def test_url_down_is_detected_and_can_start_a_task(conn: aiosqlite.Connection) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    rig = Rig(conn, Settings(), httpx.MockTransport(handler))
    await rig.auto.add_watcher(
        make_watcher("url", "down", "https://a.test", action="run", prompt="restart it")
    )
    await poll_now(rig)
    assert rig.background.submitted == [("restart it", "watcher:url-down")]


async def test_file_watcher_takes_a_baseline_then_fires_on_change(rig: Rig) -> None:
    await rig.auto.add_watcher(make_watcher("file", "changes", "~/notes.txt"))
    rig.files.evidence = {"exists": True, "mtime": 1.0, "size": 10}
    await poll_now(rig)
    await poll_now(rig)
    assert rig.notes == []
    rig.files.evidence = {"exists": True, "mtime": 2.0, "size": 12}
    await poll_now(rig)
    assert [n.body for n in rig.notes] == ["~/notes.txt changed"]
    assert rig.files.runs[0] == {"path": "~/notes.txt"}


async def test_watchers_respect_their_interval(rig: Rig) -> None:
    await rig.auto.add_watcher(make_watcher("file", "exists", "~/a", every_seconds=600))
    await rig.auto.poll_watchers()
    await rig.auto.poll_watchers()
    assert len(rig.files.runs) == 1


async def test_a_capability_turned_off_pauses_the_watcher(rig: Rig) -> None:
    policies = dict(rig.settings.tools.policies) | {Capability.FS_READ: "deny"}
    tools = rig.settings.tools.model_copy(update={"policies": policies})
    rig.settings = rig.settings.model_copy(update={"tools": tools})
    await rig.auto.add_watcher(make_watcher("file", "exists", "~/a"))
    await poll_now(rig)
    [(saved, _)] = await rig.store.watchers()
    assert saved.status == "paused" and rig.files.runs == []


async def test_missing_tools_mark_the_watcher_as_error(rig: Rig) -> None:
    await rig.auto.add_watcher(make_watcher("window", "exists", "Zoom"))
    await poll_now(rig)
    [(saved, _)] = await rig.store.watchers()
    assert saved.status == "error" and "aren't running" in saved.last_detail
