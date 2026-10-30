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
    # 7: knowledge graph (people, places and what's known about them; see core/knowledge)
    """
    CREATE TABLE kg_entity (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        mentions INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE kg_alias (
        entity_id TEXT NOT NULL REFERENCES kg_entity(id) ON DELETE CASCADE,
        alias_norm TEXT NOT NULL,
        alias_raw TEXT NOT NULL,
        PRIMARY KEY (entity_id, alias_norm)
    );
    CREATE INDEX kg_alias_norm ON kg_alias(alias_norm);
    CREATE TABLE kg_fact (
        id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL REFERENCES kg_entity(id) ON DELETE CASCADE,
        predicate TEXT NOT NULL,
        object_id TEXT REFERENCES kg_entity(id) ON DELETE CASCADE,
        object_text TEXT,
        valid_from REAL NOT NULL,
        recorded_at REAL NOT NULL,
        superseded_at REAL,
        superseded_by TEXT,
        confidence REAL NOT NULL,
        origin TEXT NOT NULL,
        quote TEXT NOT NULL DEFAULT '',
        hits INTEGER NOT NULL DEFAULT 0,
        last_used_at REAL
    );
    CREATE INDEX kg_fact_subject ON kg_fact(subject_id, superseded_at);
    CREATE INDEX kg_fact_object ON kg_fact(object_id);
    CREATE TABLE kg_tombstone (
        key TEXT PRIMARY KEY,
        created_at REAL NOT NULL
    );
    """,
    # 8: projects the user builds with another AI (see core/projects)
    """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        idea TEXT NOT NULL DEFAULT '',
        learned TEXT NOT NULL DEFAULT '[]',
        brief TEXT NOT NULL DEFAULT '',
        built_with TEXT NOT NULL DEFAULT '',
        watch_window TEXT NOT NULL DEFAULT '',
        working_marker TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
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
