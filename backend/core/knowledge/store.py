"""The knowledge graph in SQLite: entities (people, places, apps…), what's known about them as
facts, and what was forgotten on purpose.

Facts are never overwritten. When a one-valued fact changes ("lives in Bangalore" → "lives in
Chennai") the old one is marked superseded and kept, so history survives; "forget" is different:
it deletes for real and remembers a fingerprint so the same fact isn't learned again."""

import hashlib
import time
import uuid
from collections.abc import Iterable, Sequence

import aiosqlite

from core.knowledge.schema import SELF_ID, Entity, Fact, norm

FACT_COLUMNS = (
    "id, subject_id, predicate, object_id, object_text, valid_from, recorded_at, superseded_at,"
    " confidence, origin, quote, hits, last_used_at"
)
MAX_TEXT = 160
"""Longest value kept for a text fact: a short phrase, never a pasted paragraph."""


def _fact(row: aiosqlite.Row) -> Fact:
    return Fact(
        id=row["id"],
        subject_id=row["subject_id"],
        predicate=row["predicate"],
        object_id=row["object_id"],
        object_text=row["object_text"],
        valid_from=row["valid_from"],
        recorded_at=row["recorded_at"],
        superseded_at=row["superseded_at"],
        confidence=row["confidence"],
        origin=row["origin"],
        quote=row["quote"],
        hits=row["hits"],
        last_used_at=row["last_used_at"],
    )


def fingerprint(subject_id: str, predicate: str, object_key: str) -> str:
    return hashlib.sha256(f"{subject_id}|{predicate}|{object_key}".encode()).hexdigest()[:32]


class KnowledgeStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ---- entities -----------------------------------------------------------------------

    async def ensure_self(self) -> None:
        async with self._conn.execute("SELECT 1 FROM kg_entity WHERE id=?", (SELF_ID,)) as cur:
            if await cur.fetchone():
                return
        now = time.time()
        await self._conn.execute(
            "INSERT INTO kg_entity(id, type, name, created_at, last_seen_at)"
            " VALUES(?, 'person', 'You', ?, ?)",
            (SELF_ID, now, now),
        )
        for alias in ("me", "myself", "i", "the user"):
            await self.add_alias(SELF_ID, alias, commit=False)
        await self._conn.commit()

    async def entity(self, entity_id: str) -> Entity | None:
        async with self._conn.execute("SELECT * FROM kg_entity WHERE id=?", (entity_id,)) as cur:
            row = await cur.fetchone()
        return await self._entity(row) if row else None

    async def entities(self, ids: Iterable[str]) -> dict[str, Entity]:
        found: dict[str, Entity] = {}
        for entity_id in dict.fromkeys(ids):
            if entity := await self.entity(entity_id):
                found[entity_id] = entity
        return found

    async def _entity(self, row: aiosqlite.Row) -> Entity:
        async with self._conn.execute(
            "SELECT alias_raw FROM kg_alias WHERE entity_id=? ORDER BY rowid", (row["id"],)
        ) as cur:
            aliases = tuple(r[0] for r in await cur.fetchall())
        return Entity(
            id=row["id"],
            type=row["type"],
            name=row["name"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            mentions=row["mentions"],
            aliases=aliases,
        )

    async def add_entity(self, name: str, type_: str) -> Entity:
        now = time.time()
        entity_id = uuid.uuid4().hex[:12]
        await self._conn.execute(
            "INSERT INTO kg_entity(id, type, name, created_at, last_seen_at) VALUES(?, ?, ?, ?, ?)",
            (entity_id, type_, name.strip()[:80], now, now),
        )
        await self.add_alias(entity_id, name, commit=False)
        await self._conn.commit()
        entity = await self.entity(entity_id)
        assert entity is not None
        return entity

    async def add_alias(self, entity_id: str, alias: str, commit: bool = True) -> None:
        key = norm(alias)
        if not key:
            return
        await self._conn.execute(
            "INSERT OR IGNORE INTO kg_alias(entity_id, alias_norm, alias_raw) VALUES(?, ?, ?)",
            (entity_id, key, alias.strip()[:80]),
        )
        if commit:
            await self._conn.commit()

    async def entity_ids_for_aliases(self, keys: Sequence[str]) -> dict[str, list[str]]:
        """alias (normalised) -> the entities with that name."""
        found: dict[str, list[str]] = {}
        if not keys:
            return found
        marks = ",".join("?" * len(keys))
        async with self._conn.execute(
            f"SELECT alias_norm, entity_id FROM kg_alias WHERE alias_norm IN ({marks})",  # noqa: S608
            tuple(keys),
        ) as cur:
            for row in await cur.fetchall():
                found.setdefault(row[0], []).append(row[1])
        return found

    async def entities_with_word(self, word: str) -> list[str]:
        """Entities with a name that has this word in it ("srinith" in "Srinith Kumar")."""
        async with self._conn.execute(
            "SELECT DISTINCT entity_id FROM kg_alias"
            " WHERE alias_norm LIKE ? OR alias_norm LIKE ? OR alias_norm LIKE ?",
            (f"{word} %", f"% {word}", f"% {word} %"),
        ) as cur:
            return [r[0] for r in await cur.fetchall() if r[0] != SELF_ID]

    async def single_word_aliases(self) -> list[tuple[str, str]]:
        """(alias, entity id) for one-word names: what a mis-heard name is compared against."""
        async with self._conn.execute(
            "SELECT alias_norm, entity_id FROM kg_alias WHERE alias_norm NOT LIKE '% %'"
        ) as cur:
            return [(r[0], r[1]) for r in await cur.fetchall()]

    async def related_to_self(self, predicate: str, direction: str) -> list[str]:
        """Entities linked to the user by a predicate. direction: "in" (they are the subject),
        "out" (the user is), or "any"."""
        found: list[str] = []
        sides = {"in": (("subject_id", "object_id"),), "out": (("object_id", "subject_id"),)}.get(
            direction, (("subject_id", "object_id"), ("object_id", "subject_id"))
        )
        for other, mine in sides:
            async with self._conn.execute(
                f"SELECT {other} FROM kg_fact WHERE predicate=? AND {mine}=? AND superseded_at IS NULL",  # noqa: S608
                (predicate, SELF_ID),
            ) as cur:
                found += [r[0] for r in await cur.fetchall() if r[0]]
        return list(dict.fromkeys(found))

    async def search(self, text: str, limit: int = 20) -> list[Entity]:
        """Entities whose name or alias contains the text, most mentioned first."""
        key = norm(text)
        like = f"%{key}%" if key else "%"
        async with self._conn.execute(
            "SELECT DISTINCT e.* FROM kg_entity e JOIN kg_alias a ON a.entity_id = e.id"
            " WHERE a.alias_norm LIKE ? AND e.id != ? ORDER BY e.mentions DESC, e.last_seen_at DESC LIMIT ?",
            (like, SELF_ID, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [await self._entity(r) for r in rows]

    async def seen(self, entity_ids: Iterable[str]) -> None:
        now = time.time()
        for entity_id in set(entity_ids):
            await self._conn.execute(
                "UPDATE kg_entity SET mentions = mentions + 1, last_seen_at=? WHERE id=?", (now, entity_id)
            )
        await self._conn.commit()

    # ---- facts --------------------------------------------------------------------------

    async def facts(self, entity_id: str, *, current: bool = True) -> list[Fact]:
        """Facts the entity is the subject of, best first (used often, then recent)."""
        where = " AND superseded_at IS NULL" if current else ""
        async with self._conn.execute(
            f"SELECT {FACT_COLUMNS} FROM kg_fact WHERE subject_id=?{where}"  # noqa: S608
            " ORDER BY hits DESC, recorded_at DESC",
            (entity_id,),
        ) as cur:
            return [_fact(r) for r in await cur.fetchall()]

    async def facts_pointing_at(self, entity_id: str) -> list[Fact]:
        async with self._conn.execute(
            f"SELECT {FACT_COLUMNS} FROM kg_fact WHERE object_id=? AND superseded_at IS NULL"  # noqa: S608
            " ORDER BY hits DESC, recorded_at DESC",
            (entity_id,),
        ) as cur:
            return [_fact(r) for r in await cur.fetchall()]

    async def fact(self, fact_id: str) -> Fact | None:
        async with self._conn.execute(f"SELECT {FACT_COLUMNS} FROM kg_fact WHERE id=?", (fact_id,)) as cur:  # noqa: S608
            row = await cur.fetchone()
        return _fact(row) if row else None

    async def current(self, subject_id: str, predicate: str) -> list[Fact]:
        async with self._conn.execute(
            f"SELECT {FACT_COLUMNS} FROM kg_fact"  # noqa: S608
            " WHERE subject_id=? AND predicate=? AND superseded_at IS NULL",
            (subject_id, predicate),
        ) as cur:
            return [_fact(r) for r in await cur.fetchall()]

    async def add_fact(
        self,
        subject_id: str,
        predicate: str,
        *,
        object_id: str | None = None,
        object_text: str | None = None,
        confidence: float = 1.0,
        origin: str = "user_said",
        quote: str = "",
    ) -> Fact:
        now = time.time()
        fact_id = uuid.uuid4().hex[:12]
        await self._conn.execute(
            "INSERT INTO kg_fact(id, subject_id, predicate, object_id, object_text, valid_from, recorded_at,"
            " confidence, origin, quote) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact_id,
                subject_id,
                predicate,
                object_id,
                (object_text or "")[:MAX_TEXT] or None,
                now,
                now,
                confidence,
                origin,
                quote[:200],
            ),
        )
        await self._conn.commit()
        fact = await self.fact(fact_id)
        assert fact is not None
        return fact

    async def supersede(self, old_id: str, new_id: str) -> None:
        await self._conn.execute(
            "UPDATE kg_fact SET superseded_at=?, superseded_by=? WHERE id=?", (time.time(), new_id, old_id)
        )
        await self._conn.commit()

    async def used(self, fact_ids: Iterable[str]) -> None:
        now = time.time()
        for fact_id in set(fact_ids):
            await self._conn.execute(
                "UPDATE kg_fact SET hits = hits + 1, last_used_at=? WHERE id=?", (now, fact_id)
            )
        await self._conn.commit()

    async def recent(self, since: float, limit: int = 50) -> list[Fact]:
        async with self._conn.execute(
            f"SELECT {FACT_COLUMNS} FROM kg_fact WHERE recorded_at >= ? ORDER BY recorded_at DESC LIMIT ?",  # noqa: S608
            (since, limit),
        ) as cur:
            return [_fact(r) for r in await cur.fetchall()]

    # ---- forgetting ---------------------------------------------------------------------

    @staticmethod
    def key_of(fact: Fact) -> str:
        return fingerprint(fact.subject_id, fact.predicate, fact.object_id or norm(fact.object_text or ""))

    async def forgotten(self, key: str) -> bool:
        async with self._conn.execute("SELECT 1 FROM kg_tombstone WHERE key=?", (key,)) as cur:
            return await cur.fetchone() is not None

    async def delete_fact(self, fact_id: str) -> bool:
        """Delete for real, and remember the fact's fingerprint so it isn't learned again."""
        fact = await self.fact(fact_id)
        if fact is None:
            return False
        await self._conn.execute(
            "INSERT OR IGNORE INTO kg_tombstone(key, created_at) VALUES(?, ?)",
            (self.key_of(fact), time.time()),
        )
        await self._conn.execute("DELETE FROM kg_fact WHERE id=?", (fact_id,))
        await self._conn.commit()
        return True

    async def delete_entity(self, entity_id: str) -> bool:
        """Delete a person, place… and everything known about them (never the user's own entry)."""
        if entity_id == SELF_ID:
            return False
        for fact in [*await self.facts(entity_id, current=False), *await self.facts_pointing_at(entity_id)]:
            await self._conn.execute(
                "INSERT OR IGNORE INTO kg_tombstone(key, created_at) VALUES(?, ?)",
                (self.key_of(fact), time.time()),
            )
        cur = await self._conn.execute("DELETE FROM kg_entity WHERE id=?", (entity_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def rename(self, entity_id: str, name: str) -> bool:
        """Correct a name (speech recognition spells names as it hears them). The old spelling stays
        as another name for the same person, so either still finds them."""
        name = name.strip()[:80]
        if not norm(name) or entity_id == SELF_ID or await self.entity(entity_id) is None:
            return False
        await self._conn.execute("UPDATE kg_entity SET name=? WHERE id=?", (name, entity_id))
        await self.add_alias(entity_id, name, commit=False)
        await self._conn.commit()
        return True

    async def merge(self, keep_id: str, drop_id: str) -> bool:
        """Two entries for the same person: move names and facts to `keep`, drop the other."""
        if keep_id == drop_id or drop_id == SELF_ID or await self.entity(keep_id) is None:
            return False
        if await self.entity(drop_id) is None:
            return False
        await self._conn.execute(
            "INSERT OR IGNORE INTO kg_alias(entity_id, alias_norm, alias_raw)"
            " SELECT ?, alias_norm, alias_raw FROM kg_alias WHERE entity_id=?",
            (keep_id, drop_id),
        )
        await self._conn.execute("UPDATE kg_fact SET subject_id=? WHERE subject_id=?", (keep_id, drop_id))
        await self._conn.execute("UPDATE kg_fact SET object_id=? WHERE object_id=?", (keep_id, drop_id))
        await self._conn.execute(
            "UPDATE kg_entity SET mentions = mentions"
            " + (SELECT mentions FROM kg_entity WHERE id=?) WHERE id=?",
            (drop_id, keep_id),
        )
        await self._conn.execute("DELETE FROM kg_entity WHERE id=?", (drop_id,))
        await self._conn.commit()
        return True

    async def counts(self) -> dict[str, int]:
        async with self._conn.execute(
            "SELECT (SELECT COUNT(*) FROM kg_entity WHERE id != ?),"
            " (SELECT COUNT(*) FROM kg_fact WHERE superseded_at IS NULL)",
            (SELF_ID,),
        ) as cur:
            row = await cur.fetchone()
        return {"entities": int(row[0]) if row else 0, "facts": int(row[1]) if row else 0}
