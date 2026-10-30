"""Running an ability and installing what it needs, in separate processes that never see secrets."""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.env_file import CHECKOUT_ROOT, find_env_file

HOST = Path(__file__).with_name("host.py")
"""Run by path: it needs only the standard library, so it works however Jarvis was installed."""
RUN_TIMEOUT_S = 120.0
INSTALL_TIMEOUT_S = 600.0
MAX_OUTPUT_CHARS = 8_000
PASSED_ENV = frozenset(
    {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL",
     "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "SYSTEMDRIVE",
     "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT", "USERNAME"}
)  # fmt: skip
"""Only these reach an ability: no API keys, tokens or anything else from Jarvis's environment."""


@dataclass(frozen=True, slots=True)
class Ran:
    ok: bool
    output: str


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in PASSED_ENV}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _quote(path: Path) -> str:
    return '"' + str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def sandboxed(argv: list[str], folder: Path) -> list[str]:
    """On a Mac, inside macOS's sandbox: Jarvis's own data (the database, notes, other abilities) and
    its key file can't be read or written, nor the keychain; only the ability's own folder is open
    there. Everything else (the web, the user's files) works as for any program they run."""
    if sys.platform != "darwin" or shutil.which("sandbox-exec") is None:
        return argv
    data = folder.parent.parent  # <data dir>/abilities/<name>
    keys = {p for p in (CHECKOUT_ROOT / ".env", find_env_file(data)) if p is not None}
    rules = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* file-write* (subpath {_quote(data)}))",
        *(f"(deny file-read* file-write* (literal {_quote(k)}))" for k in sorted(keys)),
        f"(allow file-read* file-write* (subpath {_quote(folder)}))",  # later rules win
        '(deny mach-lookup (global-name "com.apple.SecurityServer") (global-name "com.apple.securityd"))',
    ]
    return ["sandbox-exec", "-p", "\n".join(rules), *argv]


async def run(folder: Path, args: dict[str, Any], timeout_s: float = RUN_TIMEOUT_S) -> Ran:
    argv = [sys.executable, "-I", str(HOST), str(folder)]  # -I: nothing from its folder or PYTHON*
    proc = await asyncio.create_subprocess_exec(
        *sandboxed(argv, folder),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=folder,
        env=clean_env(),
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(args).encode()), timeout_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)  # and anything it started
        await proc.wait()
        return Ran(False, f"it took longer than {timeout_s:.0f} s and was stopped")
    last = out.decode(errors="replace").strip().splitlines()[-1:] or [""]
    try:
        result = json.loads(last[0])
        return Ran(bool(result["ok"]), str(result["output"])[:MAX_OUTPUT_CHARS])
    except (ValueError, KeyError, TypeError):
        tail = err.decode(errors="replace").strip()[-1500:]
        return Ran(False, f"it crashed (exit {proc.returncode}): {tail}")


def uv() -> str | None:
    """The uv that installed Jarvis (next to its Python), else one on PATH."""
    bundled = Path(sys.executable).parents[2] / "uv" / "uv"
    return str(bundled) if bundled.is_file() else shutil.which("uv")


async def install(packages: tuple[str, ...], folder: Path) -> Ran:
    """Into the ability's own folder (lib/), never into Jarvis's own Python."""
    if not packages:
        return Ran(True, "")
    tool = uv()
    if tool is None:
        return Ran(False, "no package installer (uv) was found")
    proc = await asyncio.create_subprocess_exec(
        tool, "pip", "install", "--quiet", "--python", sys.executable, "--target", str(folder / "lib"),
        *packages,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**clean_env(), "UV_NO_CONFIG": "1"},
    )  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), INSTALL_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return Ran(False, "installing packages took too long")
    text = out.decode(errors="replace").strip()
    return Ran(proc.returncode == 0, text[-1500:])
