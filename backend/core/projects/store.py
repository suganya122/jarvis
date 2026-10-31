"""Projects in the database: one row each, kept until the user says it's done (or deletes it)."""

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, get_args

import aiosqlite

Status = Literal["understanding", "ready", "sent", "working", "answered", "reviewing", "paused", "done"]
STATUSES: tuple[str, ...] = get_args(Status)
WATCHED = ("sent", "working")
"""While the other AI has it: the background watcher looks at its window (or, for Claude Code run
by Jarvis, at its folder)."""
MAX_LEARNED = 60
COLUMNS = (
    "id, name, idea, learned, brief, built_with, watch_window, working_marker, status, detail, created_at, "
    "updated_at, rounds, review_due, session_id, folder"
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
    rounds: int = 0
    """Times Jarvis has checked the result and had it fixed (see core.projects.review)."""
    review_due: bool = False
    session_id: str = ""
    """The conversation the checks run in, so each round knows the last."""
    folder: str = ""
    """Where Claude Code builds it, when Jarvis runs Claude Code (core.projects.claude_code)."""


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
        rounds=row["rounds"],
        review_due=bool(row["review_due"]),
        session_id=row["session_id"],
        folder=row["folder"],
    )


def _key(name: str) -> str:
    return " ".join(name.casefold().split())


class ProjectStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, name: str, idea: str) -> Project:
        now = time.time()
        project = Project(
            uuid.uuid4().hex[:12], name.strip(), idea.strip(), "understanding", created_at=now, updated_at=now
        )
        await self._conn.execute(
            f"INSERT INTO projects ({COLUMNS}) "  # noqa: S608
            "VALUES (?, ?, ?, '[]', '', '', '', '', ?, '', ?, ?, 0, 0, '', '')",
            (project.id, project.name, project.idea, project.status, now, now),
        )
        await self._conn.commit()
        return project

    async def save(self, project: Project) -> None:
        """What the model writes: the content. Status and the watcher's fields change only through
        update(), so neither side can put back an older copy of the other's."""
        project.updated_at = time.time()
        project.learned = project.learned[-MAX_LEARNED:]
        await self._conn.execute(
            "UPDATE projects SET name=?, idea=?, learned=?, brief=?, built_with=?, watch_window=?, "
            "working_marker=?, detail=?, updated_at=? WHERE id=?",
            (
                project.name, project.idea, json.dumps(project.learned), project.brief, project.built_with,
                project.watch_window, project.working_marker, project.detail, project.updated_at, project.id,
            ),
        )  # fmt: skip
        await self._conn.commit()

    async def update(self, project: Project, **fields: object) -> None:
        """Change only these fields (in the database and on `project`), so the watcher and a check
        never write back an older copy over what the model saved meanwhile."""
        allowed = {"status", "detail", "rounds", "review_due", "session_id", "folder"}
        assert set(fields) <= allowed, set(fields) - allowed
        for key, value in fields.items():
            setattr(project, key, value)
        project.updated_at = time.time()
        sets = ", ".join(f"{key}=?" for key in fields)
        values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        await self._conn.execute(
            f"UPDATE projects SET {sets}, updated_at=? WHERE id=?",  # noqa: S608 - keys from the set above
            (*values, project.updated_at, project.id),
        )
        await self._conn.commit()

    async def all(self) -> list[Project]:
        async with self._conn.execute(f"SELECT {COLUMNS} FROM projects ORDER BY updated_at DESC") as cur:  # noqa: S608
            return [_row(r) for r in await cur.fetchall()]

    async def active(self) -> list[Project]:
        return [p for p in await self.all() if p.status != "done"]

    async def named(self, name: str) -> Project | None:
        """The unfinished project with exactly this name (case and spacing aside)."""
        wanted = _key(name)
        return next((p for p in await self.active() if wanted and _key(p.name) == wanted), None)

    async def find(self, name: str) -> Project | None:
        """For looking one up: exact first, then one whose name contains the words; unfinished first."""
        wanted = _key(name)
        if not wanted:
            return None
        projects = sorted(await self.all(), key=lambda p: p.status == "done")
        exact = next((p for p in projects if _key(p.name) == wanted), None)
        return exact or next((p for p in projects if wanted in _key(p.name)), None)

    async def delete(self, project_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        await self._conn.commit()
        return (cur.rowcount or 0) > 0
