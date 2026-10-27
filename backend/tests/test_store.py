import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from contracts.model import ChatMessage, Completed, ModelTier, Role, Usage
from core import db
from core.redact import Redactor
from core.store import Store


async def make_store(tmp_path: Path) -> Store:
    return Store(await db.connect(tmp_path / "t.sqlite3"), Redactor())


async def test_migrations_are_idempotent(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "t.sqlite3")
    assert await db.schema_version(conn) == len(db.MIGRATIONS)
    await db.migrate(conn)
    assert await db.schema_version(conn) == len(db.MIGRATIONS)
    await conn.close()


async def test_audit_log_is_append_only_and_redacted(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    await store.audit("user", "x", {"note": "key sk-ant-abcdefghijklmnop"})
    conn = store._conn
    async with conn.execute("SELECT detail FROM audit_log") as cur:
        row = await cur.fetchone()
    assert row is not None and "sk-ant-abc" not in row["detail"]
    with pytest.raises(sqlite3.IntegrityError):
        await conn.execute("DELETE FROM audit_log")
    with pytest.raises(sqlite3.IntegrityError):
        await conn.execute("UPDATE audit_log SET actor='evil'")
    await store.close()


async def test_history_keeps_provider_state(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    sid = await store.ensure_session(None, "first message")
    assert await store.ensure_session(sid, "ignored") == sid
    state = {"provider": "anthropic", "content": [{"type": "text", "text": "hi"}]}
    await store.append_message(sid, "t1", ChatMessage(role=Role.USER, text="hello"))
    await store.append_message(sid, "t1", ChatMessage(role=Role.ASSISTANT, text="hi", provider_state=state))
    history = await store.history(sid)
    assert [m.role for m in history] == [Role.USER, Role.ASSISTANT]
    assert history[1].provider_state == state
    assert await store.delete_session(sid)
    assert await store.history(sid) == []
    await store.close()


async def test_earlier_turns_come_back_without_their_reasoning(tmp_path: Path) -> None:
    """Claude rejects a thinking block whose earlier conversation changed (screenshots aren't
    stored, the screen context differs each turn), so replayed history leaves them out."""
    store = await make_store(tmp_path)
    sid = await store.ensure_session(None, "which screen am I on")
    reply = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "redacted_thinking", "data": "x"},
        {"type": "tool_use", "id": "c1", "name": "capture_screen", "input": {}},
    ]
    state = {"provider": "anthropic", "content": reply}
    await store.append_message(sid, "t1", ChatMessage(role=Role.USER, text="which screen am I on"))
    await store.append_message(sid, "t1", ChatMessage(role=Role.ASSISTANT, text="", provider_state=state))
    history = await store.history(sid)
    assert history[1].provider_state == {"provider": "anthropic", "content": [reply[2]]}
    await store.close()


async def test_cost_summary_buckets_by_day_and_month(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    done = Completed(
        provider="anthropic",
        model="m",
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5),
        cost_usd=0.25,
    )
    await store.record_cost("t", done)
    summary = await store.cost_summary()
    assert summary.today_usd == pytest.approx(0.25)
    assert summary.month_tokens == 15
    future = await store.cost_summary(datetime.now(UTC) + timedelta(days=40))
    assert future.today_usd == 0
    await store.close()


async def test_settings_default_then_saved(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    settings = await store.load_settings()
    assert settings.provider == "anthropic"
    await store.save_settings(settings.model_copy(update={"provider": "local"}))
    assert (await store.load_settings()).provider == "local"
    await store.close()


async def test_settings_saved_with_the_old_cheap_default_move_up_once(tmp_path: Path) -> None:
    """Every request used the smallest model, which loses the thread of a conversation. Saved
    settings that still say "cheap" (never a choice) get the balanced model once; a tier chosen
    afterwards stays."""
    store = await make_store(tmp_path)
    await store._conn.execute(
        "INSERT INTO settings(key, value) VALUES('settings', ?)",
        ('{"schema_version": 1, "default_tier": "cheap"}',),  # as saved before 0.1.17
    )
    await store._conn.commit()
    upgraded = await store.load_settings()
    assert upgraded.default_tier == "balanced"
    chosen = upgraded.model_copy(update={"default_tier": ModelTier.CHEAP})  # the user's own choice, now
    await store.save_settings(chosen)
    assert (await store.load_settings()).default_tier == "cheap"
    await store.close()
