"""Typed persistence for settings, chat history, cost ledger, and the audit log."""

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite
from pydantic import BaseModel

from contracts.model import IMAGE_OMITTED, ChatMessage, Completed, Role, ToolResultBlock
from core.config import Settings
from core.redact import Redactor

SETTINGS_KEY = "settings"


def _with_omitted_note(text: str, had_images: bool) -> str:
    return f"{text}\n\n{IMAGE_OMITTED}" if had_images else text


def _stored_block(block: ToolResultBlock) -> dict[str, Any]:
    """Tool results are kept, screenshots are not (privacy, and old images aren't re-sent)."""
    data = block.model_dump(exclude={"images"})
    data["output"] = _with_omitted_note(block.output, bool(block.images))
    return data


class SessionInfo(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float


class StoredMessage(BaseModel):
    role: Role
    text: str
    trace_id: str
    created_at: float


class AuditEntry(BaseModel):
    id: int
    created_at: float
    trace_id: str | None
    actor: str
    action: str
    detail: dict[str, Any]


class CostSummary(BaseModel):
    today_usd: float
    month_usd: float
    today_tokens: int
    month_tokens: int


class Store:
    def __init__(self, conn: aiosqlite.Connection, redactor: Redactor) -> None:
        self._conn = conn
        self._redact = redactor

    @property
    def conn(self) -> aiosqlite.Connection:
        return self._conn

    async def close(self) -> None:
        await self._conn.close()

    # ---- settings ---------------------------------------------------------

    async def load_settings(self) -> Settings:
        async with self._conn.execute("SELECT value FROM settings WHERE key=?", (SETTINGS_KEY,)) as cur:
            row = await cur.fetchone()
        return Settings.model_validate_json(row["value"]) if row else Settings()

    async def save_settings(self, settings: Settings) -> None:
        await self._conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SETTINGS_KEY, settings.model_dump_json()),
        )
        await self._conn.commit()

    # ---- sessions and messages -------------------------------------------

    async def ensure_session(self, session_id: str | None, first_text: str) -> str:
        if session_id:
            async with self._conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)) as cur:
                if await cur.fetchone():
                    return session_id
        new_id = uuid.uuid4().hex
        now = time.time()
        title = " ".join(first_text.split())[:60] or "New chat"
        await self._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at) VALUES(?, ?, ?, ?)",
            (new_id, title, now, now),
        )
        await self._conn.commit()
        return new_id

    async def list_sessions(self, limit: int = 100) -> list[SessionInfo]:
        async with self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [SessionInfo(**dict(row)) for row in await cur.fetchall()]

    async def delete_session(self, session_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def history(self, session_id: str, limit: int = 200) -> list[ChatMessage]:
        async with self._conn.execute(
            "SELECT role, text, provider_state, tool_results FROM ("
            " SELECT id, role, text, provider_state, tool_results FROM messages WHERE session_id=?"
            " ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (session_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        messages = [
            ChatMessage(
                role=Role(row["role"]),
                text=row["text"],
                provider_state=json.loads(row["provider_state"]) if row["provider_state"] else None,
                tool_results=tuple(ToolResultBlock(**b) for b in json.loads(row["tool_results"] or "[]")),
            )
            for row in rows
        ]
        # A cut-off window must start at a plain user message, never mid tool exchange.
        while messages and (messages[0].role is not Role.USER or messages[0].tool_results):
            messages.pop(0)
        return messages

    async def transcript(self, session_id: str) -> list[StoredMessage]:
        """What a person sees: no tool-result plumbing, no empty tool-calling turns."""
        async with self._conn.execute(
            "SELECT role, text, trace_id, created_at FROM messages WHERE session_id=?"
            " AND tool_results IS NULL AND text != '' ORDER BY id",
            (session_id,),
        ) as cur:
            return [StoredMessage(**dict(row)) for row in await cur.fetchall()]

    async def append_messages(self, session_id: str, trace_id: str, *messages: ChatMessage) -> None:
        """Appends atomically, so a tool call is never stored without its result."""
        now = time.time()
        rows = [
            (
                session_id,
                m.role.value,
                _with_omitted_note(m.text, bool(m.images)),
                json.dumps(m.provider_state) if m.provider_state else None,
                json.dumps([_stored_block(b) for b in m.tool_results]) if m.tool_results else None,
                trace_id,
                now,
            )
            for m in messages
        ]
        await self._conn.executemany(
            "INSERT INTO messages(session_id, role, text, provider_state, tool_results, trace_id,"
            " created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self._conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
        await self._conn.commit()

    async def append_message(self, session_id: str, trace_id: str, message: ChatMessage) -> None:
        await self.append_messages(session_id, trace_id, message)

    async def is_tainted(self, session_id: str) -> bool:
        async with self._conn.execute("SELECT tainted FROM sessions WHERE id=?", (session_id,)) as cur:
            row = await cur.fetchone()
        return bool(row and row["tainted"])

    async def mark_tainted(self, session_id: str) -> None:
        await self._conn.execute("UPDATE sessions SET tainted=1 WHERE id=?", (session_id,))
        await self._conn.commit()

    # ---- cost ------------------------------------------------------------

    async def record_cost(self, trace_id: str, done: Completed) -> None:
        u = done.usage
        await self._conn.execute(
            "INSERT INTO cost_ledger(trace_id, created_at, provider, model, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, cost_usd)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trace_id,
                time.time(),
                done.provider,
                done.model,
                u.input_tokens,
                u.output_tokens,
                u.cache_read_tokens,
                u.cache_write_tokens,
                done.cost_usd,
            ),
        )
        await self._conn.commit()

    async def cost_summary(self, now: datetime | None = None) -> CostSummary:
        now = (now or datetime.now(UTC)).astimezone()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        query = (
            "SELECT COALESCE(SUM(cost_usd), 0) AS usd,"
            " COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens), 0)"
            " AS tokens FROM cost_ledger WHERE created_at >= ?"
        )
        totals = []
        for start in (day_start, month_start):
            async with self._conn.execute(query, (start,)) as cur:
                row = await cur.fetchone()
            totals.append((float(row["usd"]), int(row["tokens"])) if row else (0.0, 0))
        return CostSummary(
            today_usd=totals[0][0],
            today_tokens=totals[0][1],
            month_usd=totals[1][0],
            month_tokens=totals[1][1],
        )

    # ---- audit -----------------------------------------------------------

    async def list_audit(self, limit: int = 100, before_id: int | None = None) -> list[AuditEntry]:
        async with self._conn.execute(
            "SELECT id, created_at, trace_id, actor, action, detail FROM audit_log"
            " WHERE id < ? ORDER BY id DESC LIMIT ?",
            (before_id if before_id is not None else 2**62, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [AuditEntry(**{**dict(row), "detail": json.loads(row["detail"])}) for row in rows]

    async def audit(
        self, actor: str, action: str, detail: dict[str, Any], trace_id: str | None = None
    ) -> None:
        text = self._redact(json.dumps(detail, default=str))
        await self._conn.execute(
            "INSERT INTO audit_log(created_at, trace_id, actor, action, detail) VALUES(?, ?, ?, ?, ?)",
            (time.time(), trace_id, actor, action, text),
        )
        await self._conn.commit()
