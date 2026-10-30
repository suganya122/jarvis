"""Open URLs in the browser and files with their default app, per OS."""

import asyncio
import os
import stat
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field

from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolResult
from workers.os_tools.base import Args, Env, WorkerTool
from workers.os_tools.paths import PathDenied

# Opening these would run code, so open_path refuses them.
EXECUTABLE_SUFFIXES = frozenset(
    [
        ".exe",
        ".msi",
        ".bat",
        ".cmd",
        ".com",
        ".scr",
        ".ps1",
        ".vbs",
        ".js",
        ".jse",
        ".wsf",
        ".lnk",
        ".reg",
        ".jar",
        ".py",
        ".pl",
        ".rb",
        ".sh",
        ".bash",
        ".zsh",
        ".command",
        ".app",
        ".scpt",
        ".applescript",
        ".workflow",
        ".desktop",
        ".appimage",
        ".deb",
        ".rpm",
        ".pkg",
        ".dmg",
        ".run",
        ".bin",
    ]
)


class UrlArgs(Args):
    url: str = Field(max_length=2_048)


class OpenUrl(WorkerTool[UrlArgs]):
    name = "open_url"
    description = (
        "Open an http(s) link in the user's own default browser (where they are signed in): "
        "use it for anything they want to see, watch or use."
    )
    args_model = UrlArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.NET_FETCH,)

    def assess(self, args: UrlArgs, env: Env) -> ToolAssessment:
        parsed = urlparse(args.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ToolAssessment(risk=self.risk, summary=f"Open {args.url[:80]}", error="only http(s) links")
        return ToolAssessment(
            risk=self.risk, summary=f"Open {parsed.netloc}{parsed.path[:60]} in your browser"
        )

    async def run(self, args: UrlArgs, env: Env) -> ToolResult:
        opened = await asyncio.to_thread(webbrowser.open, args.url, 2)
        return ToolResult(ok=opened, output="Opened in the browser." if opened else "No browser available.")


class PathArgs(Args):
    path: str


def _is_executable(path: Path) -> bool:
    if path.suffix.lower() in EXECUTABLE_SUFFIXES:
        return True
    if sys.platform != "win32" and path.is_file():
        return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return False


def open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opens a document, not a program
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen(  # noqa: S603 - fixed opener, path already confined and non-executable
        [opener, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class OpenPath(WorkerTool[PathArgs]):
    name = "open_path"
    description = "Open a document or folder with its default app. Programs and scripts are refused."
    args_model = PathArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: PathArgs, env: Env) -> ToolAssessment:
        try:
            path = env.paths.resolve(args.path)
        except PathDenied as exc:
            return ToolAssessment(risk=self.risk, summary=f"Open {args.path}", error=str(exc))
        shown = env.display(path)
        if not path.exists():
            return ToolAssessment(risk=self.risk, summary=f"Open {shown}", error="no such file or folder")
        if _is_executable(path):
            return ToolAssessment(
                risk=self.risk, summary=f"Open {shown}", error="programs and scripts can't be opened"
            )
        return ToolAssessment(risk=self.risk, summary=f"Open {shown} with its default app")

    async def run(self, args: PathArgs, env: Env) -> ToolResult:
        path = env.paths.resolve(args.path)
        await asyncio.to_thread(open_with_default_app, path)
        return ToolResult(ok=True, output=f"Opened {env.display(path)}.", evidence={"path": str(path)})
