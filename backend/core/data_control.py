"""Export everything, delete everything.

Deletion removes only the files this app creates (never a whole folder by pattern), so a
data folder pointed somewhere unusual (ASSISTANT_DATA_DIR) can't take other files with it.
"""

import contextlib
import json
import logging
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiosqlite

from contracts import APP_NAME, APP_VERSION
from core import paths
from core.autostart import Autostart
from core.secret_store import KeychainUnavailable, SecretStore

log = logging.getLogger(__name__)
JSON_COLUMNS = {"body", "detail", "state", "provider_state", "tool_calls", "tool_results", "value"}
MODELS_DIR = "models"
PLUGINS_DIR = "plugins"


def _decode(column: str, value: Any) -> Any:
    if column in JSON_COLUMNS and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


async def export_all(conn: aiosqlite.Connection) -> dict[str, Any]:
    """Every table, as rows. There are no secrets in the database (the API key is in the
    OS keychain), and the auth token is a file, so neither is exported."""
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ) as cur:
        tables = [row[0] for row in await cur.fetchall()]
    data: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        async with conn.execute(f'SELECT * FROM "{table}"') as cur:  # noqa: S608  (names from sqlite_master)
            columns = [c[0] for c in cur.description]
            data[table] = [
                {c: _decode(c, v) for c, v in zip(columns, row, strict=True)} for row in await cur.fetchall()
            ]
    return {"app": APP_NAME, "app_version": APP_VERSION, "exported_at": time.time(), "tables": data}


def app_files(data_dir: Path) -> list[Path]:
    db = paths.db_path(data_dir)
    candidates = [
        db,
        db.with_name(db.name + "-wal"),
        db.with_name(db.name + "-shm"),
        paths.token_path(data_dir),
        data_dir / MODELS_DIR,
        data_dir / PLUGINS_DIR,
        data_dir / "browser",  # the assistant's browser profile (its cookies and logins)
        data_dir / "abilities",  # abilities it built for itself (their code and packages)
    ]
    return [p for p in candidates if p.exists()]


def wipe(
    data_dir: Path, secrets: SecretStore, autostart: Autostart, plugin_secrets: Sequence[tuple[str, str]] = ()
) -> list[str]:
    """Delete the app's data, keychain entry and login item. Returns what was removed.
    The database must be closed first."""
    removed: list[str] = []
    try:
        if secrets.anthropic_key_source() == "keychain":
            secrets.delete_anthropic_key()
            removed.append("keychain entry")
        for plugin, name in plugin_secrets:
            if secrets.get_plugin_secret(plugin, name):
                secrets.delete_plugin_secret(plugin, name)
                removed.append(f"{plugin} {name}")
    except KeychainUnavailable:
        log.warning("keychain unavailable; keychain entries (if any) were not removed")
    if autostart.supported and autostart.enabled():
        autostart.disable()
        removed.append("start at login")
    for path in app_files(data_dir):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        removed.append(path.name)
    with contextlib.suppress(OSError):
        data_dir.rmdir()  # only if nothing else is in it
    return removed
