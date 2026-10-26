"""Restricted command runner: no shell, allowlisted programs, confined paths, timeouts."""

import asyncio
import contextlib
import os
import shlex
import shutil
import signal
import sys
from pathlib import Path

from pydantic import Field

from contracts.common import Capability, RiskLevel, Trust
from contracts.tools import ToolAssessment, ToolResult
from workers.os_tools.base import Args, Env, WorkerTool, truncate
from workers.os_tools.paths import PathDenied

# Never runnable, even if a user adds them to the allowlist: privilege, disk, power,
# deletion, and anything that can run arbitrary other programs.
HARD_BLOCKED = frozenset(
    {
        "sudo", "su", "doas", "pkexec", "runas",
        "rm", "rmdir", "del", "erase", "shred", "unlink", "srm",
        "dd", "mkfs", "fdisk", "sfdisk", "parted", "diskpart", "diskutil", "format", "mount", "umount",
        "shutdown", "reboot", "halt", "poweroff", "init", "systemctl", "launchctl", "telinit",
        "chmod", "chown", "chgrp", "icacls", "takeown",
        "kill", "killall", "pkill", "taskkill",
        "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh",
        "python", "python3", "node", "deno", "bun", "perl", "ruby", "php", "lua", "osascript", "wscript",
        "cscript", "env", "xargs", "nohup", "timeout", "nice", "exec", "eval", "find", "crontab", "at",
        "ssh", "scp", "sftp", "nc", "ncat", "netcat", "telnet", "curl", "wget",
    }
)  # fmt: skip
MAX_ARG_CHARS = 4_096
SAFE_ENV_KEYS = ("PATH", "HOME", "USERPROFILE", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")


def program_name(argv0: str) -> str:
    name = argv0.lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        name = name.removesuffix(suffix)
    return name


def _looks_like_path(arg: str) -> bool:
    return (
        arg.startswith(("~", ".", "/", "\\")) or "/" in arg or "\\" in arg or (len(arg) > 1 and arg[1] == ":")
    )


class CommandArgs(Args):
    argv: list[str] = Field(
        min_length=1, max_length=64, description="Program and its arguments; no shell syntax."
    )
    cwd: str | None = Field(default=None, description="Working folder; defaults to the home folder.")


class RunCommand(WorkerTool[CommandArgs]):
    name = "run_command"
    description = (
        "Run one allowlisted program with arguments (no shell, no pipes, no globbing) inside "
        "the allowed folders. Output is untrusted data."
    )
    args_model = CommandArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.SHELL_RUN,)

    def check(self, args: CommandArgs, env: Env) -> tuple[str, Path]:
        """Returns (resolved program, cwd) or raises ValueError / PathDenied."""
        argv0 = args.argv[0]
        if os.sep in argv0 or "/" in argv0 or "\\" in argv0:
            raise ValueError("give the program name only, not a path")
        name = program_name(argv0)
        if name in HARD_BLOCKED:
            raise ValueError(f"'{argv0}' is never allowed")
        allowed = {program_name(p) for p in env.config.shell_allowlist}
        if name not in allowed:
            raise ValueError(f"'{argv0}' is not in the command allowlist (Settings)")
        program = shutil.which(argv0)
        if program is None:
            raise ValueError(f"'{argv0}' is not installed")
        cwd = env.paths.resolve(args.cwd or "~")
        if not cwd.is_dir():
            raise ValueError("working folder does not exist")
        for arg in args.argv[1:]:
            if len(arg) > MAX_ARG_CHARS or "\x00" in arg:
                raise ValueError("argument too long or invalid")
            value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
            if _looks_like_path(value) or (cwd / value).exists():
                target = value if Path(os.path.expanduser(value)).is_absolute() else str(cwd / value)
                env.paths.resolve(target)
        return program, cwd

    def assess(self, args: CommandArgs, env: Env) -> ToolAssessment:
        shown = shlex.join(args.argv)
        try:
            _, cwd = self.check(args, env)
        except (ValueError, PathDenied) as exc:
            return ToolAssessment(risk=self.risk, summary=f"Run `{shown}`", error=str(exc))
        return ToolAssessment(risk=self.risk, summary=f"Run `{shown}` in {env.display(cwd)}")

    async def run(self, args: CommandArgs, env: Env) -> ToolResult:
        program, cwd = self.check(args, env)
        child_env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
        proc = await asyncio.create_subprocess_exec(
            program,
            *args.argv[1:],
            cwd=cwd,
            env=child_env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
        )
        limit = env.config.max_output_chars
        try:
            output, capped = await asyncio.wait_for(
                _read_capped(proc, limit * 4), env.config.command_timeout_s
            )
            code = await proc.wait()
        except TimeoutError:
            _kill(proc)
            await proc.wait()
            return ToolResult(ok=False, output=f"Stopped after {env.config.command_timeout_s:.0f} s timeout.")
        except asyncio.CancelledError:
            _kill(proc)
            raise
        text = output.decode("utf-8", errors="replace")
        if capped:
            text += "\n[output cut off]"
        return ToolResult(
            ok=code == 0,
            output=truncate(f"exit code {code}\n{text}", limit),
            trust=Trust.UNTRUSTED,
            evidence={"exit_code": code},
        )


async def _read_capped(proc: asyncio.subprocess.Process, max_bytes: int) -> tuple[bytes, bool]:
    assert proc.stdout is not None
    chunks: list[bytes] = []
    total = 0
    while chunk := await proc.stdout.read(65_536):
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            _kill(proc)
            return b"".join(chunks)[:max_bytes], True
    return b"".join(chunks), False


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
