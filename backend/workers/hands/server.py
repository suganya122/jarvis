"""Tool table for the hands worker."""

from typing import Any, TextIO

from contracts.capture import CaptureError
from workers.common.base import WorkerTool
from workers.common.server import Server
from workers.hands.context import ActiveContextTool
from workers.hands.errors import AtspiError
from workers.hands.fake import FakeError
from workers.hands.keys import KeyError_
from workers.hands.locate import LocateError
from workers.hands.screen_tools import CaptureScreen, HighlightArea
from workers.hands.setup_tool import AccessibilityStatus
from workers.hands.tools import (
    ChooseMenu,
    ClickElement,
    FindElements,
    FocusWindow,
    HandsEnv,
    HandsUnavailable,
    InsertText,
    ListWindows,
    PressKeys,
    ReadElement,
    Scroll,
    TypeText,
)

EXPECTED_ERRORS = (AtspiError, FakeError, KeyError_, HandsUnavailable, CaptureError, LocateError)


def all_tools() -> dict[str, WorkerTool[Any, Any]]:
    tools: list[WorkerTool[Any, Any]] = [
        ListWindows(),
        FindElements(),
        ClickElement(),
        TypeText(),
        PressKeys(),
        Scroll(),
        FocusWindow(),
        ChooseMenu(),
        ReadElement(),
        InsertText(),
        AccessibilityStatus(),
        CaptureScreen(),
        HighlightArea(),
        ActiveContextTool(),
    ]
    return {t.name: t for t in tools}


def make_server(out: TextIO) -> Server:
    return Server(out, all_tools(), HandsEnv, expected_errors=EXPECTED_ERRORS)
