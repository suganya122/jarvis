"""The `.env` file: where you paste your Anthropic API key.

Looked for in this order: `ASSISTANT_ENV_FILE`, a `.env` next to the source checkout, then
one in the app data folder. Values here win over the OS keychain and the shell, so the file
you're editing is the one that counts. It's read once at start (restart to change the key).
The key is never printed or logged; the file is kept readable by you only.
"""

import logging
import os
import stat
from pathlib import Path

from dotenv import dotenv_values

log = logging.getLogger(__name__)
CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = """\
# Your secrets live here. This file stays on this computer and is never committed or logged.
# Paste your key right after the equals sign (no quotes needed), save, then restart the app.
# Get one at https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY=
"""


def find_env_file(data_dir: Path) -> Path | None:
    explicit = os.environ.get("ASSISTANT_ENV_FILE")
    candidates = [Path(explicit)] if explicit else [CHECKOUT_ROOT / ".env", data_dir / ".env"]
    return next((p for p in candidates if p.is_file()), None)


def create_template(path: Path) -> None:
    """A new .env with an empty key line, readable only by you. Never overwrites."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(TEMPLATE)


def load(path: Path | None) -> dict[str, str]:
    """The non-empty values in the file ({} without one). Tightens loose permissions."""
    if path is None:
        return {}
    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            try:
                path.chmod(0o600)
                log.warning("%s was readable by other users; it is now private to you", path.name)
            except OSError:
                log.warning("%s is readable by other users and couldn't be fixed: chmod 600 it", path.name)
    values = {k: v.strip() for k, v in dotenv_values(path).items() if v and v.strip()}
    if problem := key_problem(values.get("ANTHROPIC_API_KEY", "")):
        log.warning("%s: ANTHROPIC_API_KEY %s", path.name, problem)  # never the key itself
    return values


def key_problem(key: str) -> str:
    """What looks wrong with a pasted key, or "" (it may still be revoked: only Anthropic knows)."""
    if not key:
        return ""
    if any(c.isspace() for c in key) or key.count("sk-ant-") > 1:
        return "looks like two keys or extra text pasted together: keep only one key on the line"
    if not key.startswith("sk-ant-"):
        return "doesn't start with sk-ant-: copy the whole key from console.anthropic.com"
    if len(key) > 150:
        return f"is {len(key)} characters long (a key is about 108): something extra was pasted"
    return ""
