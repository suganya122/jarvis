"""Memory: facts the user asked to remember, stored locally and given to the model.

Selection is deliberately simple and cache-friendly: when every fact fits the budget, all
of them go in, in a stable order, so the prompt only changes when memory changes. Only a
large memory falls back to picking the facts that share the most words with the request.
"""

import re
import time
import uuid

import aiosqlite

from contracts.memory import MAX_MEMORY_CHARS, Memory

HEADER = (
    "Notes the user asked you to remember (saved on this computer). Use them when relevant. Where "
    "one says how they want you to work (which browser or app to use, what to say or not say), "
    "always do it that way unless they now ask otherwise:"
)
_STOP = frozenset(
    "the a an and or of to in on at for is are was were be my me i you your it this that with "
    "what when where who how do does did can could would should please about from".split()
)


class MemoryStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def all(self) -> list[Memory]:
        async with self._conn.execute("SELECT body FROM memories ORDER BY created_at, id") as cur:
            return [Memory.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get(self, memory_id: str) -> Memory | None:
        async with self._conn.execute("SELECT body FROM memories WHERE id=?", (memory_id,)) as cur:
            row = await cur.fetchone()
        return Memory.model_validate_json(row[0]) if row else None

    async def add(self, text: str, source: str) -> Memory:
        now = time.time()
        fields = {"memory_id": uuid.uuid4().hex[:12], "text": tidy(text), "source": source}
        memory = Memory.model_validate(fields | {"created_at": now, "updated_at": now})
        await self._save(memory)
        return memory

    async def update(self, memory_id: str, text: str) -> Memory | None:
        memory = await self.get(memory_id)
        if memory is None:
            return None
        updated = Memory.model_validate(memory.model_dump() | {"text": tidy(text), "updated_at": time.time()})
        await self._save(updated)
        return updated

    async def delete(self, memory_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def find(self, query: str) -> list[Memory]:
        """Memories mentioning every word of the query (for 'forget …')."""
        words = _words(query)
        return [m for m in await self.all() if words and words <= _words(m.text)]

    async def _save(self, memory: Memory) -> None:
        await self._conn.execute(
            "INSERT INTO memories(id, body, created_at) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            (memory.memory_id, memory.model_dump_json(), memory.created_at),
        )
        await self._conn.commit()


def tidy(text: str) -> str:
    text = " ".join(text.split()).strip(" .")
    return (text[:1].upper() + text[1:])[:MAX_MEMORY_CHARS]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.casefold()) if len(w) > 2 and w not in _STOP}


def select(memories: list[Memory], request: str, max_chars: int) -> list[Memory]:
    """Which memories to show the model for this request, in their saved order."""
    if sum(len(m.text) + 3 for m in memories) <= max_chars:
        return memories
    wanted = _words(request)
    ranked = sorted(
        enumerate(memories), key=lambda im: (len(wanted & _words(im[1].text)), im[1].created_at), reverse=True
    )
    picked: list[tuple[int, Memory]] = []
    used = 0
    for index, memory in ranked:
        if used + len(memory.text) + 3 > max_chars:
            continue
        picked.append((index, memory))
        used += len(memory.text) + 3
    return [m for _, m in sorted(picked, key=lambda im: im[0])]


def render(memories: list[Memory]) -> str:
    if not memories:
        return ""
    return HEADER + "\n" + "\n".join(f"- {m.text}" for m in memories)
