"""Active context with fake displays and windows: two monitors, mixed DPI, negative
coordinates, a window on another desktop, an unplugged display, a blocklisted window."""

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from contracts.context import ActiveContext, Display
from contracts.hands import ElementInfo, Rect, WindowInfo
from contracts.model import ToolCallRequested
from contracts.worker import WorkerConfig
from core.context import ContextService, names_another_display, render, retarget
from core.tool_exec import ChatPermissions
from workers.common.displays import name_displays
from workers.hands.capture import RawCapture, crop_box
from workers.hands.context import ActiveContextTool, build
from workers.hands.env import HandsEnv
from workers.hands.fake import FakeHands, FakeWindow, fixture_window
from workers.hands.screen_tools import CaptureArgs, CaptureScreen

# Screen 1 is left of the primary display (negative x) and is a 200% (HiDPI) display.
LEFT, MAIN = (-1920, 0, 1920, 1080, 2.0, False), (0, 0, 2560, 1440, 1.0, True)
DISPLAYS = tuple(name_displays([MAIN, LEFT]))


def win(wid: str, app: str, title: str, x: int, focused: bool = False, on_screen: bool = True) -> WindowInfo:
    return WindowInfo(
        window_id=wid,
        app=app,
        title=title,
        bounds=Rect(x=x, y=100, width=800, height=600),
        focused=focused,
        on_screen=on_screen,
    )


WINDOWS = [win("w1", "Chrome", "Inbox - Gmail", 200, focused=True), win("w2", "Editor", "notes.txt", -1500)]


def snapshot(**kw: Any) -> ActiveContext:
    args: dict[str, Any] = {
        "displays": DISPLAYS,
        "pointer": (900, 500),
        "pointer_reliable": True,
        "windows": WINDOWS,
        "control": None,
        "selection": "",
        "clipboard": None,
        "blocked_app": "",
        "now": 1000.0,
        "context_id": "c1",
    } | kw
    return build(**args)


def test_displays_are_named_left_to_right_and_found_by_pointer() -> None:
    assert [(d.name, d.x, d.scale, d.primary) for d in DISPLAYS] == [
        ("Screen 1", -1920, 2.0, False),
        ("Screen 2", 0, 1.0, True),
    ]
    ctx = snapshot()
    assert ctx.display and ctx.display.name == "Screen 2" and ctx.window and ctx.window.window_id == "w1"
    assert ctx.label() == "Screen 2 · Chrome · Inbox - Gmail"
    assert snapshot(pointer=(-400, 300)).display.name == "Screen 1"  # type: ignore[union-attr]  # negative coordinates
    assert snapshot(pointer=(100, 1300)).display.name == "Screen 2"  # type: ignore[union-attr]  # in a gap: nearest


def test_without_a_reliable_pointer_this_screen_is_the_focused_windows() -> None:
    ctx = snapshot(pointer=(-400, 300), pointer_reliable=False)  # e.g. Wayland
    assert ctx.display and ctx.display.name == "Screen 2" and ctx.pointer is None
    assert any("pointer position isn't available" in limit for limit in ctx.limits)


def test_a_blocklisted_focused_app_is_never_read() -> None:
    control = ElementInfo(element_id="e1", role="password text", name="Master password")
    ctx = snapshot(
        windows=[win("w2", "Editor", "notes.txt", -1500)],
        blocked_app="Bitwarden",
        control=control,
        selection="hunter2",
    )
    assert ctx.window is None and ctx.control is None and ctx.selection == ""
    assert ctx.label() == "Screen 2 · Bitwarden (never read)"
    assert "Bitwarden" in render(ctx) and "never read or capture it" in render(ctx)


def test_selection_and_clipboard_are_shared_only_when_safe() -> None:
    ctx = snapshot(selection="  Quarterly numbers look good  ")
    assert ctx.selection == "Quarterly numbers look good" and ctx.clipboard is None
    secret = snapshot(selection="card 4111 1111 1111 1111", clipboard="sk-ant-abcdefghijklmnopqrstuvwx")
    assert secret.selection == "" and secret.clipboard == ""
    assert sum("looked like a password" in limit for limit in secret.limits) == 2
    assert "«Quarterly numbers look good»" in render(ctx)


def test_mixed_dpi_and_negative_coordinates_crop_the_right_pixels() -> None:
    displays = list(DISPLAYS)
    logical = (4480, 1440)  # both displays side by side, in desktop units
    assert crop_box((0, 0, 2560, 1440), displays, logical) == (1920, 0, 4480, 1440)
    assert crop_box((-1920, 0, 1920, 1080), displays, logical) == (0, 0, 1920, 1080)
    physical = (8960, 2880)  # a capture taken at 2x (HiDPI)
    assert crop_box((0, 0, 2560, 1440), displays, physical) == (3840, 0, 8960, 2880)


# ---- the hands worker side, with fake hands and displays ---------------------------------


def env_with(
    windows: list[FakeWindow],
    blocked: tuple[str, ...] = (),
    displays: tuple[Display, ...] = DISPLAYS,
    pointer: tuple[int, int] = (900, 500),
) -> tuple[HandsEnv, list[Any]]:
    config = WorkerConfig(
        allowed_roots=(),
        denied_paths=(),
        shell_allowlist=(),
        max_output_chars=8000,
        command_timeout_s=5,
        blocked_apps=blocked,
    )
    grabs: list[Any] = []

    def grab(kind: str, bounds: Any) -> RawCapture:
        grabs.append((kind, bounds))
        return RawCapture(Image.new("RGB", (4480, 1440)), "x11", desktop_width=4480, desktop_height=1440)

    env = HandsEnv(config, hands=FakeHands(windows, blocked_apps=blocked), grab=grab)  # type: ignore[arg-type]
    env.displays = lambda: list(displays)
    env.pointer = lambda: pointer
    env.pointer_reliable = lambda: True
    env.clipboard = lambda: "copied text"
    return env, grabs


def chrome(active: bool = True, on_screen: bool = True) -> FakeWindow:
    w = fixture_window("fake|chrome", active=active)
    w.app, w.title, w.bounds, w.on_screen = (
        "Chrome",
        "Inbox - Gmail",
        Rect(x=200, y=100, width=800, height=600),
        on_screen,
    )
    w.focus, w.selection = next(iter(w.nodes)), "the selected paragraph"
    return w


async def test_the_snapshot_tool_gives_short_ids_and_respects_the_clipboard_switch() -> None:
    env, _ = env_with([chrome()])
    tool = ActiveContextTool()
    result = await tool.run(tool.args_model(), env)
    ctx = ActiveContext.model_validate(result.evidence["context"])
    assert ctx.window and ctx.window.window_id == "w1" and ctx.control and ctx.control.element_id == "e1"
    assert ctx.selection == "the selected paragraph" and ctx.clipboard is None
    with_clip = await tool.run(tool.args_model(clipboard=True), env)
    assert with_clip.evidence["context"]["clipboard"] == "copied text"


async def test_blocklisted_focused_window_through_the_tool() -> None:
    vault = fixture_window("fake|vault", active=True)
    vault.app = "Bitwarden"
    env, _ = env_with([vault, chrome(active=False)], blocked=("bitwarden",))
    result = await ActiveContextTool().run(ActiveContextTool.args_model(), env)
    assert (
        result.evidence["context"]["blocked_app"] == "Bitwarden"
        and result.evidence["context"]["window"] is None
    )
    assert result.output == "Screen 2 · Bitwarden (never read)"


async def test_this_screen_only_and_an_unplugged_display() -> None:
    env, _grabs = env_with([chrome()])
    shot = await CaptureScreen().run(CaptureArgs(target="screen", display_id="d2"), env)
    assert shot.ok and "of Screen 2 (2560x1440 at 0,0)" in shot.output
    assert shot.evidence["capture"]["screen_x"] == 1920  # cropped to that display only
    unplugged, grabs2 = env_with([chrome()], displays=DISPLAYS[:1])  # Screen 2 was unplugged mid-task
    gone = await CaptureScreen().run(CaptureArgs(target="screen", display_id="d2"), unplugged)
    assert not gone.ok and "isn't connected anymore" in gone.output and grabs2 == []  # nothing else captured


async def test_a_window_on_another_desktop_is_not_captured() -> None:
    env, grabs = env_with([chrome(on_screen=False)])
    env.ids.add("w", "fake|chrome", "Chrome — Inbox")
    shot = await CaptureScreen().run(CaptureArgs(target="window", window_id="w1"), env)
    assert not shot.ok and "another desktop or minimized" in shot.output and grabs == []


# ---- core: the guard and corrections ------------------------------------------------------


def service(tmp_path: Path) -> ContextService:
    return ContextService(None, lambda: None, None, clock=lambda: 1000.0)  # type: ignore[arg-type]


def perms_with(ctx: ActiveContext, request: str) -> ChatPermissions:
    perms = ChatPermissions(session_id="s", tainted=False, context=ctx, request=request)
    return perms


def capture(**args: str) -> ToolCallRequested:
    return ToolCallRequested(call_id="c", name="capture_screen", input=dict(args))


def test_the_guard_keeps_screenshots_on_this_screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = service(tmp_path)
    monkeypatch.setattr(svc, "pinned", lambda perms: perms.context)
    ctx = snapshot()
    filled = svc.guard(capture(target="screen"), perms_with(ctx, "read this screen"))
    assert isinstance(filled, ToolCallRequested) and filled.input["display_id"] == "d2"
    refused = svc.guard(capture(target="screen", display_id="d1"), perms_with(ctx, "read this screen"))
    assert isinstance(refused, str) and "Screen 2" in refused
    named = svc.guard(
        capture(target="screen", display_id="d1"), perms_with(ctx, "what's on my other screen?")
    )
    assert isinstance(named, ToolCallRequested)


@pytest.mark.parametrize(
    "text", ["my other screen", "the left monitor", "screen 1", "both screens", "all of my screens"]
)
def test_naming_another_display(text: str) -> None:
    assert names_another_display(text)


def test_corrections_retarget() -> None:
    ctx = snapshot()
    other = retarget(ctx, "no, the other one")
    assert (
        other
        and other.display
        and other.display.name == "Screen 1"
        and other.window
        and other.window.app == "Editor"
    )
    assert retarget(ctx, "the left screen").display.name == "Screen 1"  # type: ignore[union-attr]
    assert retarget(ctx, "Screen 2") is None  # already there
    window = retarget(ctx, "the other window")
    assert window and window.window and window.window.window_id == "w2"
    assert retarget(ctx, "open the other file") is None  # not a correction
