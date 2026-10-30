"""Running an ability and installing what it needs, in separate processes that never see secrets."""

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOST = Path(__file__).with_name("host.py")
"""Run by path: it needs only the standard library, so it works however Jarvis was installed."""
RUN_TIMEOUT_S = 120.0
INSTALL_TIMEOUT_S = 600.0
MAX_OUTPUT_CHARS = 8_000
PASSED_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "SystemRoot", "TEMP")
"""Only these reach an ability: no API keys, tokens or anything else from Jarvis's environment."""


@dataclass(frozen=True, slots=True)
class Ran:
    ok: bool
    output: str


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in PASSED_ENV}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


async def run(folder: Path, args: dict[str, Any], timeout_s: float = RUN_TIMEOUT_S) -> Ran:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",  # nothing from the folder it runs in, the user's site-packages or PYTHON* variables
        str(HOST),
        str(folder),
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
        proc.kill()
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
