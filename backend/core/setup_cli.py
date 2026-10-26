"""Setup steps the one-line installer runs (and you can run again any time):

- `set-key`: save the Anthropic API key (read from stdin, never echoed) into the app data
  folder's `.env`, private to you.
- `setup-voice [--model ID]`: download the speech model that suits this computer (checked
  against its pinned SHA-256) and switch it on, so hands-free voice works at first start.
- `autostart on|off [--exec PATH]`: start at login (macOS passes its Jarvis.app launcher).
"""

import re
import sys
from collections.abc import Callable
from pathlib import Path

from contracts.ws import DownloadProgress
from core import db, env_file, paths
from core.autostart import Autostart, launch_command
from core.model_files import BY_ID, ModelFiles, recommended_model
from core.redact import Redactor
from core.store import Store

KEY_PATTERN = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$")
Say = Callable[[str], None]


def set_key(data_dir: Path, key: str, say: Say = print) -> int:
    key = key.strip()
    if not KEY_PATTERN.match(key):
        say("That doesn't look like an Anthropic API key (they start with sk-ant-). Nothing was saved.")
        return 1
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / ".env"
    if not path.exists():
        env_file.create_template(path)
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("ANTHROPIC_API_KEY=")
    ]
    lines.append(f"ANTHROPIC_API_KEY={key}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    say(f"Saved your API key in {path} (readable only by you).")
    return 0


async def setup_voice(data_dir: Path, model_id: str | None = None, say: Say = print) -> int:
    spec = BY_ID.get(model_id or "") or recommended_model()
    files = ModelFiles(data_dir / "models")
    if not files.installed(spec):
        say(f"Downloading the speech model: {spec.title} ({spec.size // 1_000_000} MB, {spec.license})…")
        shown = -10
        problem: list[str] = []

        async def report(progress: DownloadProgress) -> None:
            nonlocal shown
            if progress.state == "error":
                problem.append(progress.error or "download failed")
            percent = progress.done * 100 // max(progress.total, 1)
            if progress.state == "downloading" and percent >= shown + 10:
                shown = percent
                say(f"  {percent}%")

        files.start(spec, report)
        await files.wait(spec.id)
        if problem or not files.installed(spec):
            say(f"The speech model couldn't be downloaded: {problem[0] if problem else 'unknown error'}")
            return 1
    conn = await db.connect(paths.db_path(data_dir))
    try:
        store = Store(conn, Redactor())
        settings = await store.load_settings()
        voice = settings.voice.model_copy(update={"stt_model": spec.id, "hands_free": True})
        await store.save_settings(settings.model_copy(update={"voice": voice}))
    finally:
        await conn.close()
    say(f"Speech recognition is ready: {spec.title}. It runs on this computer.")
    return 0


def autostart(on: bool, exec_path: str | None = None, say: Say = print) -> int:
    starter = Autostart(command=[exec_path] if exec_path else launch_command())
    if not starter.supported:
        say("Start at login isn't supported on this system.")
        return 1
    if on:
        starter.enable()
        say("It will start when you log in.")
    else:
        starter.disable()
        say("It won't start at login any more.")
    return 0


def read_key_from_stdin() -> str:
    return sys.stdin.readline()
