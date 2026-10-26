"""Local auth token: random, generated at first run, readable only by the user."""

import os
import secrets
from pathlib import Path

TOKEN_BYTES = 32


def load_or_create_token(path: Path) -> str:
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(TOKEN_BYTES)
    # Create with 0600 so other local users can't read it (no-op on Windows ACLs).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


def token_matches(expected: str, presented: str | None) -> bool:
    if not presented:
        return False
    return secrets.compare_digest(expected.encode(), presented.encode())


def bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    scheme, _, value = header_value.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value else None
