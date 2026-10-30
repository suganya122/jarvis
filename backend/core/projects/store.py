"""Projects in the database: one row each, kept until the user says it's done (or deletes it)."""

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, get_args

import aiosqlite

Status = Literal["understanding", "ready", "sent", "working", "answered", "paused", "done"]
STATUSES: tuple[str, ...] = get_args(Status)
WATCHED = ("sent", "working")
"""While the other AI has it: the background watcher looks at its window."""
MAX_LEARNED = 60
COLUMNS = (
    "id, name, idea, learned, brief, built_with, watch_window, working_marker, status, detail, created_at, "
    "updated_at"
)


@dataclass(slots=True)
class Project:
    id: str
    name: str
    idea: str
    status: str
    learned: list[str] = field(default_factory=list)
    brief: str = ""
    built_with: str = ""
    watch_window: str = ""
    working_marker: str = ""
    detail: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


def _row(row: aiosqlite.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        idea=row["idea"],
        status=row["status"],
        learned=list(json.loads(row["learned"] or "[]")),
        brief=row["brief"],
        built_with=row["built_with"],
        watch_window=row["watch_window"],
        working_marker=row["working_marker"],
        detail=row["detail"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ProjectStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, name: str, idea: str) -> Project:
        now = time.time()
        project = Project(
            uuid.uuid4().hex[:12], name.strip(), idea.strip(), "understanding", created_at=now, updated_at=now
        )
        await self._conn.execute(
            f"INSERT INTO projects ({COLUMNS}) VALUES (?, ?, ?, '[]', '', '', '', '', ?, '', ?, ?)",  # noqa: S608
            (project.id, project.name, project.idea, project.status, now, now),
        )
        await self._conn.commit()
        return project

    async def save(self, project: Project) -> None:
        project.updated_at = time.time()
        project.learned = project.learned[-MAX_LEARNED:]
        await self._conn.execute(
            "UPDATE projects SET name=?, idea=?, learned=?, brief=?, built_with=?, watch_window=?, "
            "working_marker=?, status=?, detail=?, updated_at=? WHERE id=?",
            (
                project.name, project.idea, json.dumps(project.learned), project.brief, project.built_with,
                project.watch_window, project.working_marker, project.status, project.detail,
                project.updated_at, project.id,
            ),
        )  # fmt: skip
        await self._conn.commit()

    async def all(self) -> list[Project]:
        async with self._conn.execute(f"SELECT {COLUMNS} FROM projects ORDER BY updated_at DESC") as cur:  # noqa: S608
            return [_row(r) for r in await cur.fetchall()]

    async def active(self) -> list[Project]:
        return [p for p in await self.all() if p.status != "done"]

    async def find(self, name: str) -> Project | None:
        """By name: exact first, then the most recent one whose name contains it (or the reverse)."""
        wanted = " ".join(name.casefold().split())
        projects = await self.all()
        for p in projects:
            if p.name.casefold() == wanted:
                return p
        return next((p for p in projects if wanted in p.name.casefold() or p.name.casefold() in wanted), None)

    async def delete(self, project_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        await self._conn.commit()
        return (cur.rowcount or 0) > 0
