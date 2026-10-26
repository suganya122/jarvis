"""Tool table for the os_tools worker."""

import sys
from typing import Any, TextIO

from workers.common.base import WorkerTool
from workers.common.server import Server
from workers.os_tools.app_tools import MediaControl, OpenApp
from workers.os_tools.base import Env
from workers.os_tools.fs_tools import ListDirectory, ReadTextFile, WriteTextFile
from workers.os_tools.mac_automation import MacAutomation
from workers.os_tools.notify_tools import DesktopNotify, FileInfo
from workers.os_tools.open_tools import OpenPath, OpenUrl
from workers.os_tools.paths import PathDenied
from workers.os_tools.shell_tool import RunCommand
from workers.os_tools.system_tool import SystemInfo
from workers.os_tools.window_tools import ArrangeWindows


def all_tools() -> dict[str, WorkerTool[Any, Any]]:
    tools: list[WorkerTool[Any, Any]] = [
        ListDirectory(),
        ReadTextFile(),
        WriteTextFile(),
        RunCommand(),
        OpenUrl(),
        OpenPath(),
        SystemInfo(),
        OpenApp(),
        ArrangeWindows(),
        MediaControl(),
        FileInfo(),
        DesktopNotify(),
        *([MacAutomation()] if sys.platform == "darwin" else []),
    ]
    return {t.name: t for t in tools}


def make_server(out: TextIO) -> Server:
    return Server(out, all_tools(), Env, expected_errors=(PathDenied,))
