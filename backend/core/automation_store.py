"""Persistence for background tasks, routines and watchers."""

import json
from typing import Any

import aiosqlite

from contracts.automation import Routine, TaskInfo, Watcher

LIVE = ("queued", "running", "waiting_approval")


class AutomationStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ---- tasks ---------------------------------------------------------------

    async def save_task(self, task: TaskInfo) -> None:
        await self._conn.execute(
            "INSERT INTO tasks(id, status, body, created_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, body=excluded.body",
            (task.task_id, task.status, task.model_dump_json(), task.created_at),
        )
        await self._conn.commit()

    async def tasks(self, limit: int = 50) -> list[TaskInfo]:
        async with self._conn.execute(
            "SELECT body FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [TaskInfo.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def task(self, task_id: str) -> TaskInfo | None:
        async with self._conn.execute("SELECT body FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
        return TaskInfo.model_validate_json(row[0]) if row else None

    async def interrupt_live_tasks(self) -> list[TaskInfo]:
        """After a crash or restart: tasks that were running wait for the user to resume them."""
        marks = ",".join("?" * len(LIVE))
        async with self._conn.execute(f"SELECT body FROM tasks WHERE status IN ({marks})", LIVE) as cur:  # noqa: S608
            live = [TaskInfo.model_validate_json(r[0]) for r in await cur.fetchall()]
        interrupted = [t.model_copy(update={"status": "interrupted", "current_tool": ""}) for t in live]
        for task in interrupted:
            await self.save_task(task)
        return interrupted

    async def delete_task(self, task_id: str) -> None:
        await self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        await self._conn.commit()

    # ---- routines ------------------------------------------------------------

    async def save_routine(self, routine: Routine) -> None:
        await self._conn.execute(
            "INSERT INTO routines(id, body) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            (routine.routine_id, routine.model_dump_json()),
        )
        await self._conn.commit()

    async def routines(self) -> list[Routine]:
        async with self._conn.execute("SELECT body FROM routines") as cur:
            items = [Routine.model_validate_json(r[0]) for r in await cur.fetchall()]
        return sorted(items, key=lambda r: r.created_at)

    async def delete_routine(self, routine_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM routines WHERE id=?", (routine_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    # ---- watchers ------------------------------------------------------------

    async def save_watcher(self, watcher: Watcher, state: dict[str, Any] | None = None) -> None:
        await self._conn.execute(
            "INSERT INTO watchers(id, body, state) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET body=excluded.body, state=COALESCE(?, watchers.state)",
            (
                watcher.watcher_id,
                watcher.model_dump_json(),
                json.dumps(state or {}),
                json.dumps(state) if state is not None else None,
            ),
        )
        await self._conn.commit()

    async def watchers(self) -> list[tuple[Watcher, dict[str, Any]]]:
        async with self._conn.execute("SELECT body, state FROM watchers") as cur:
            rows = await cur.fetchall()
        items = [(Watcher.model_validate_json(r[0]), json.loads(r[1])) for r in rows]
        return sorted(items, key=lambda w: w[0].created_at)

    async def delete_watcher(self, watcher_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM watchers WHERE id=?", (watcher_id,))
        await self._conn.commit()
        return cur.rowcount > 0
