"""File status (for watchers) and desktop notifications (internal: never offered to the model)."""

import asyncio
import shutil
import subprocess
import sys

from pydantic import Field

from contracts import APP_NAME
from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolResult
from workers.os_tools.base import Args, Env, WorkerTool
from workers.os_tools.paths import PathDenied


class FileArgs(Args):
    path: str


class FileInfo(WorkerTool[FileArgs]):
    name = "file_info"
    description = "Check whether a file or folder exists, and its size and last change time."
    args_model = FileArgs
    risk = RiskLevel.READ
    capabilities = (Capability.FS_READ,)

    def assess(self, args: FileArgs, env: Env) -> ToolAssessment:
        try:
            path = env.paths.resolve(args.path)
        except PathDenied as exc:
            return ToolAssessment(risk=self.risk, summary=f"Check {args.path}", error=str(exc))
        return ToolAssessment(risk=self.risk, summary=f"Check {env.display(path)}")

    async def run(self, args: FileArgs, env: Env) -> ToolResult:
        path = env.paths.resolve(args.path)
        exists = path.exists()
        stat = path.stat() if exists else None
        evidence = {
            "exists": exists,
            "size": stat.st_size if stat else None,
            "mtime": stat.st_mtime if stat else None,
            "is_dir": path.is_dir(),
        }
        text = f"{env.display(path)}: " + (f"exists, {evidence['size']} bytes" if exists else "doesn't exist")
        return ToolResult(ok=True, output=text, evidence=evidence)


class NotifyArgs(Args):
    title: str = Field(max_length=120)
    body: str = Field(default="", max_length=500)


def _command(title: str, body: str) -> list[str] | None:
    if sys.platform == "darwin":
        script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
        return ["osascript", "-e", script, title, body]
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        return ["notify-send", f"--app-name={APP_NAME}", title, body]
    return None


class DesktopNotify(WorkerTool[NotifyArgs]):
    name = "desktop_notify"
    description = "Show a desktop notification (used by reminders and watchers, not by the model)."
    args_model = NotifyArgs
    risk = RiskLevel.READ
    capabilities = ()

    def assess(self, args: NotifyArgs, env: Env) -> ToolAssessment:
        error = None if _command(args.title, args.body) else "desktop notifications aren't available here"
        return ToolAssessment(risk=self.risk, summary="Show a notification", error=error)

    async def run(self, args: NotifyArgs, env: Env) -> ToolResult:
        argv = _command(args.title, args.body)
        if argv is None:
            return ToolResult(ok=False, output="desktop notifications aren't available here")
        # Text goes in as separate arguments, never through a shell or a script string.
        done = await asyncio.to_thread(subprocess.run, argv, capture_output=True, timeout=5, check=False)
        return ToolResult(ok=done.returncode == 0, output="shown" if done.returncode == 0 else "not shown")
