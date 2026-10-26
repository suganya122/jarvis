"""Open installed apps by name; control media playback and volume."""

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import Field

from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolResult
from workers.os_tools.apps import App, AppIndex, best_match
from workers.os_tools.base import Args, Env, WorkerTool
from workers.os_tools.media import MediaAction, MediaUnavailable, available, perform

INDEX = AppIndex()


class AppArgs(Args):
    name: str = Field(min_length=1, max_length=80, description="App name, e.g. 'Firefox' or 'calculator'.")


def launch(app: App) -> None:
    path = Path(app.path)
    if sys.platform == "win32":
        import os

        os.startfile(path)  # noqa: S606 - a Start Menu shortcut
        return
    if sys.platform == "darwin":
        argv = ["open", "-a", str(path)]
    elif shutil.which("gio"):
        argv = ["gio", "launch", str(path)]
    else:
        argv = ["gtk-launch", path.stem]
    subprocess.Popen(  # noqa: S603 - fixed launcher, path from the OS app index
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class OpenApp(WorkerTool[AppArgs]):
    name = "open_app"
    description = "Open an installed application by name."
    args_model = AppArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: AppArgs, env: Env) -> ToolAssessment:
        app, ties = best_match(args.name, INDEX.apps())
        if app is None:
            error = (
                f"several apps match: {', '.join(a.name for a in ties[:5])}"
                if ties
                else f"no installed app called '{args.name}'"
            )
            return ToolAssessment(risk=self.risk, summary=f"Open {args.name}", error=error)
        return ToolAssessment(risk=self.risk, summary=f"Open {app.name}")

    async def run(self, args: AppArgs, env: Env) -> ToolResult:
        app, _ = best_match(args.name, INDEX.apps())
        if app is None:
            return ToolResult(ok=False, output=f"No installed app called '{args.name}'.")
        await asyncio.to_thread(launch, app)
        return ToolResult(ok=True, output=f"Opened {app.name}.", evidence={"app": app.name})


class MediaArgs(Args):
    action: MediaAction


ACTION_LABELS: dict[str, str] = {
    "play_pause": "Play or pause media",
    "next": "Skip to the next track",
    "previous": "Go to the previous track",
    "stop": "Stop playback",
    "volume_up": "Turn the volume up",
    "volume_down": "Turn the volume down",
    "mute": "Mute or unmute",
}


class MediaControl(WorkerTool[MediaArgs]):
    name = "media_control"
    description = "Play/pause, skip tracks, change or mute the system volume."
    args_model = MediaArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: MediaArgs, env: Env) -> ToolAssessment:
        problem = available()
        return ToolAssessment(risk=self.risk, summary=ACTION_LABELS[args.action], error=problem)

    async def run(self, args: MediaArgs, env: Env) -> ToolResult:
        try:
            await asyncio.to_thread(perform, args.action)
        except MediaUnavailable as exc:
            return ToolResult(ok=False, output=str(exc))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ToolResult(ok=False, output="No media player responded.")
        return ToolResult(ok=True, output=f"{ACTION_LABELS[args.action]}: done.")
