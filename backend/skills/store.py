"""Skill persistence (one JSON document per skill, in the main SQLite database)."""

import time

import aiosqlite

from contracts.skills import Skill


class SkillStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def all(self) -> list[Skill]:
        async with self._conn.execute("SELECT body FROM skills ORDER BY updated_at DESC") as cur:
            return [Skill.model_validate_json(row[0]) for row in await cur.fetchall()]

    async def get(self, skill_id: str) -> Skill | None:
        async with self._conn.execute("SELECT body FROM skills WHERE id=?", (skill_id,)) as cur:
            row = await cur.fetchone()
        return Skill.model_validate_json(row[0]) if row else None

    async def by_signature(self, signature: str) -> Skill | None:
        async with self._conn.execute("SELECT body FROM skills WHERE signature=?", (signature,)) as cur:
            row = await cur.fetchone()
        return Skill.model_validate_json(row[0]) if row else None

    async def save(self, skill: Skill) -> None:
        await self._conn.execute(
            "INSERT INTO skills(id, signature, body, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET signature=excluded.signature, body=excluded.body, "
            "updated_at=excluded.updated_at",
            (skill.skill_id, skill.signature, skill.model_dump_json(), time.time()),
        )
        await self._conn.commit()

    async def delete(self, skill_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM skills WHERE id=?", (skill_id,))
        await self._conn.commit()
        return cur.rowcount > 0
