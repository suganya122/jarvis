"""OS-standard locations for app data."""

import os
from pathlib import Path

from platformdirs import user_data_dir

from contracts import APP_ID, ENV_PREFIX

DATA_DIR_ENV = f"{ENV_PREFIX}DATA_DIR"


def data_dir() -> Path:
    """App data folder; overridable for tests and portable installs."""
    override = os.environ.get(DATA_DIR_ENV)
    path = Path(override) if override else Path(user_data_dir(APP_ID, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path(base: Path) -> Path:
    return base / "assistant.sqlite3"


def token_path(base: Path) -> Path:
    return base / "auth_token"
