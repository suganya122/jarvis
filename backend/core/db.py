"""SQLite access with forward-only migrations. WAL mode keeps data safe across crashes."""

from pathlib import Path

import aiosqlite

MIGRATIONS: list[str] = [
    # 1: settings, chat sessions, messages, cost ledger, append-only audit log
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        text TEXT NOT NULL,
        provider_state TEXT,
        trace_id TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX messages_by_session ON messages(session_id, id);
    CREATE TABLE cost_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cache_read_tokens INTEGER NOT NULL,
        cache_write_tokens INTEGER NOT NULL,
        cost_usd REAL NOT NULL
    );
    CREATE INDEX cost_by_time ON cost_ledger(created_at);
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at REAL NOT NULL,
        trace_id TEXT,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT NOT NULL
    );
    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
    """,
    # 2: tool results on messages; per-chat taint once untrusted content entered it
    """
    ALTER TABLE messages ADD COLUMN tool_results TEXT;
    ALTER TABLE sessions ADD COLUMN tainted INTEGER NOT NULL DEFAULT 0;
    """,
    # 3: learned skills (one JSON document per skill; signature indexed for merging)
    """
    CREATE TABLE skills (
        id TEXT PRIMARY KEY,
        signature TEXT NOT NULL UNIQUE,
        body TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    # 4: background tasks, routines, watchers (JSON bodies; tasks survive crashes)
    """
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE routines (
        id TEXT PRIMARY KEY,
        body TEXT NOT NULL
    );
    CREATE TABLE watchers (
        id TEXT PRIMARY KEY,
        body TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT '{}'
    );
    """,
    # 5: memory (facts the user asked to remember)
    """
    CREATE TABLE memories (
        id TEXT PRIMARY KEY,
        body TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    """,
    # 6: which chat each chat-app conversation continues
    """
    CREATE TABLE channel_conversations (
        channel TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        PRIMARY KEY (channel, conversation_id)
    );
    """,
]


async def connect(path: Path | str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await migrate(conn)
    return conn


async def schema_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def migrate(conn: aiosqlite.Connection) -> None:
    current = await schema_version(conn)
    if current > len(MIGRATIONS):
        raise RuntimeError(f"Database schema {current} is newer than this app supports")
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        await conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={version};\nCOMMIT;")
