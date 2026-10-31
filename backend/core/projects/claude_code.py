"""Claude Code, run by Jarvis in the project's folder: nothing typed into Terminal, no screen read.

Typing it into Terminal and watching the window failed in two ways: the window's text was read
from its oldest end, so "esc to interrupt" was never seen and it looked finished while it worked;
and Claude Code stopped to ask before every command it ran (the app, its tests), each stop taken
for the end. Now Jarvis starts `claude -p` itself, in ~/Projects/<name>, with a permission hook
that answers with Jarvis's own rules (workers/os_tools/claude_code_hook.py), so it works through
to the end. Its progress is written to .jarvis/ in the folder: the watcher reads it to know when
it's finished and what it reported, and a Terminal window shows it for the user to watch. The
next message (fixes after a check) continues the same conversation (`claude -c -p`).
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

RUN_DIR = ".jarvis"
HOOK = Path(__file__).parents[2] / "workers" / "os_tools" / "claude_code_hook.py"
"""Claude Code's permission hook: run by it, with Jarvis's rules for commands (not imported here)."""
PROGRESS = Path(__file__).with_name("cc_progress.py")
FINISH = (
    "\n\nWork only in this folder. Before you stop, run the tests and the build (add tests where there "
    "are none) and fix every error and failing test. End with a short report: what you built, how to "
    "open or run it, and the results of the tests and the build."
)
"""Added to the brief: the first check is Claude Code's own."""
WHERE_IT_IS = ("~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin")
"""Where the installers put `claude`: an app started from the Dock gets only a bare PATH."""
SECRETS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
"""Not passed on: Claude Code uses the user's own sign-in, never Jarvis's key."""
State = Literal["none", "working", "finished", "stopped"]
_waiting: set["asyncio.Task[int]"] = set()


@dataclass(slots=True)
class Run:
    """What Claude Code is doing in a folder, from its files."""

    state: State
    report: str = ""
    """Its final message (finished), or what it printed when it stopped (stopped)."""
    failed: bool = False
    refused: list[str] = field(default_factory=list)
    """Steps Jarvis's rules refused: they need the user's yes."""
    cost_usd: float = 0.0


def folder_for(name: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40] or "project"
    return Path.home() / "Projects" / slug


def claude_binary() -> str | None:
    path = os.pathsep.join([*(os.path.expanduser(p) for p in WHERE_IT_IS), os.environ.get("PATH", "")])
    return shutil.which("claude", path=path)


def prepare(folder: Path, protected: list[str]) -> None:
    """The folder, Jarvis's notes in it, and the permission hook in Claude Code's local settings."""
    (folder / RUN_DIR).mkdir(parents=True, exist_ok=True)
    (folder / RUN_DIR / "hook.json").write_text(json.dumps({"protected": protected}))
    settings_file = folder / ".claude" / "settings.local.json"
    settings_file.parent.mkdir(exist_ok=True)
    try:
        settings = json.loads(settings_file.read_text())
    except (OSError, ValueError):
        settings = {}
    hook = {"type": "command", "command": f'"{sys.executable}" -I "{HOOK}"', "timeout": 30}
    hooks = settings.setdefault("hooks", {})
    others = [h for h in hooks.get("PreToolUse", []) if str(HOOK) not in json.dumps(h)]
    hooks["PreToolUse"] = [*others, {"matcher": "*", "hooks": [hook]}]
    settings_file.write_text(json.dumps(settings, indent=2))


def status(folder: Path) -> Run:
    run_dir = folder / RUN_DIR
    try:
        started = json.loads((run_dir / "run.json").read_text())
    except (OSError, ValueError):
        return Run("none")
    refused = _refused(run_dir)
    result = _result(run_dir / "stream.jsonl")
    if result is not None:
        return Run(
            "finished",
            str(result.get("result") or ""),
            failed=bool(result.get("is_error")),
            refused=refused,
            cost_usd=float(str(result.get("total_cost_usd") or 0.0)),
        )
    if _alive(int(started.get("pid", 0))):
        return Run("working", refused=refused)
    try:
        said = (run_dir / "stderr.log").read_text(errors="replace")[-1500:].strip()
    except OSError:
        said = ""
    return Run("stopped", said or "It ended without a report.", failed=True, refused=refused)


async def launch(folder: Path, prompt: str, carry_on: bool, show: bool = True) -> str | None:
    """Start Claude Code on `prompt` in the folder; None when it's running, else why it couldn't."""
    binary = claude_binary()
    if binary is None:
        return (
            "Claude Code isn't installed. Install it with run_shell: "
            "`curl -fsSL https://claude.ai/install.sh | bash`, then start it again."
        )
    if status(folder).state == "working":
        return "Claude Code is still working there; wait for it to finish."
    run_dir = folder / RUN_DIR
    argv = [binary, *(["-c"] if carry_on else []), "-p", prompt, "--output-format", "stream-json",
            "--verbose", "--permission-mode", "acceptEdits"]  # fmt: skip
    env = _env()
    with (run_dir / "stream.jsonl").open("wb") as out, (run_dir / "stderr.log").open("wb") as err:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=folder,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            env=env,
            start_new_session=True,
        )
    (run_dir / "run.json").write_text(
        json.dumps({"pid": proc.pid, "started": time.time(), "carry_on": carry_on})
    )
    task = asyncio.create_task(proc.wait())  # so a finished run doesn't linger as a zombie
    _waiting.add(task)
    task.add_done_callback(_waiting.discard)
    if show:
        await asyncio.to_thread(_show, run_dir / "stream.jsonl")
    return None


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SECRETS}
    env["PATH"] = os.pathsep.join([*(os.path.expanduser(p) for p in WHERE_IT_IS), env.get("PATH", "")])
    return env


def _show(stream: Path) -> None:
    """A Terminal window with its progress, for the user to watch (Jarvis never types in it)."""
    follow = f"{_quote(sys.executable)} -I {_quote(str(PROGRESS))} {_quote(str(stream))}"
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        if sys.platform == "darwin":
            script = f'tell application "Terminal" to do script "{_applescript(follow)}"'
            tell = ["/usr/bin/osascript", "-e", script, "-e", 'tell application "Terminal" to activate']
            subprocess.run(tell, capture_output=True, timeout=15, check=False)  # noqa: S603 - paths quoted
        elif terminal := shutil.which("gnome-terminal"):
            subprocess.Popen([terminal, "--", "sh", "-c", follow], start_new_session=True,  # noqa: S603
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _result(stream: Path) -> dict[str, object] | None:
    try:
        lines = stream.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines[-50:]):
        with contextlib.suppress(ValueError):
            event = json.loads(line)
            if isinstance(event, dict) and event.get("type") == "result":
                return event
    return None


def _refused(run_dir: Path) -> list[str]:
    try:
        lines = (run_dir / "refused.jsonl").read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-10:]:
        with contextlib.suppress(ValueError):
            item = json.loads(line)
            given = item.get("input") or {}
            what = given.get("command") or given.get("file_path") or item.get("tool")
            out.append(f"{what} ({item.get('why', '').split(':')[0]})")
    return out


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
