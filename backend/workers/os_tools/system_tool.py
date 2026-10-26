"""System information (read-only)."""

import os
import platform
import shutil
from datetime import datetime

from contracts.common import RiskLevel
from contracts.tools import ToolAssessment, ToolResult
from workers.os_tools.base import Args, Env, WorkerTool, human_size


class NoArgs(Args):
    pass


class SystemInfo(WorkerTool[NoArgs]):
    name = "system_info"
    description = "Get the operating system, CPU count, free disk space, and local time."
    args_model = NoArgs
    risk = RiskLevel.READ
    capabilities = ()

    def assess(self, args: NoArgs, env: Env) -> ToolAssessment:
        return ToolAssessment(risk=self.risk, summary="Read basic system information")

    async def run(self, args: NoArgs, env: Env) -> ToolResult:
        now = datetime.now().astimezone()
        lines = [
            f"os: {platform.system()} {platform.release()} ({platform.machine()})",
            f"cpus: {os.cpu_count()}",
            f"local time: {now:%Y-%m-%d %H:%M} {now.tzname()}",
        ]
        for root in env.paths.roots[:3]:
            usage = shutil.disk_usage(root)
            lines.append(
                f"disk at {env.display(root)}: {human_size(usage.free)} free of {human_size(usage.total)}"
            )
        return ToolResult(ok=True, output="\n".join(lines))
