"""Screen capture and highlight: image handling, tools, privacy, and the real X11 path."""

import asyncio
import base64
import io
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from PIL import Image

from contracts.capture import CaptureError, CaptureKind
from contracts.common import Trust
from contracts.worker import WorkerConfig
from core.worker_host import WorkerHost
from tests.a11y_session import a11y_session, missing_requirements
from workers.hands import capture as capture_mod
from workers.hands import screen_tools as S
from workers.hands import tools as T
from workers.hands.capture import RawCapture
from workers.hands.fake import FakeHands, fixture_window
from workers.hands.images import PREVIEW_EDGE, fit, model_and_preview

CONFIG = WorkerConfig(
    allowed_roots=(), denied_paths=(), shell_allowlist=(), max_output_chars=4000, command_timeout_s=5,
    capture_max_edge=1280,
)  # fmt: skip


def decode(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def fake_grab(size: tuple[int, int] = (2560, 1600)):  # type: ignore[no-untyped-def]
    calls: list[CaptureKind] = []

    def grab(kind: CaptureKind, bounds: tuple[int, int, int, int] | None) -> RawCapture:
        calls.append(kind)
        image = Image.new("RGB", size, (30, 60, 90))
        return RawCapture(image, "test", desktop_width=size[0], desktop_height=size[1])

    return grab, calls


# ---- images -------------------------------------------------------------------


def test_fit_and_encode() -> None:
    big = Image.new("RGB", (3000, 1500), "white")
    small, scale = fit(big, 1280)
    assert small.size == (1280, 640) and scale == pytest.approx(3000 / 1280)
    same, one = fit(Image.new("RGB", (800, 600)), 1280)
    assert same.size == (800, 600) and one == 1.0
    model, preview, _ = model_and_preview(big, 1280)
    assert (model.width, model.height) == (1280, 640) and model.media_type == "image/jpeg"
    assert max(preview.width, preview.height) == PREVIEW_EDGE
    assert decode(model.data_b64).size == (1280, 640)


# ---- tools on the fake backend --------------------------------------------------


async def test_capture_returns_image_preview_and_remembers_geometry() -> None:
    grab, calls = fake_grab()
    env = T.HandsEnv(CONFIG, hands=FakeHands(), grab=grab)
    result = await S.CaptureScreen().run(S.CaptureArgs(), env)
    assert result.ok and result.trust is Trust.UNTRUSTED and calls == ["screen"]
    assert len(result.images) == 1 and (result.images[0].width, result.images[0].height) == (1280, 800)
    assert result.preview is not None and result.preview.width == PREVIEW_EDGE
    info = result.evidence["capture"]
    assert info["scale"] == pytest.approx(2.0) and env.captures[info["capture_id"]].desktop_width == 2560


async def test_highlight_maps_image_pixels_to_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    grab, _ = fake_grab()
    env = T.HandsEnv(CONFIG, hands=FakeHands(), grab=grab)
    shown: list[tuple[object, ...]] = []
    monkeypatch.setattr(S, "unavailable_reason", lambda: None)
    monkeypatch.setattr(env.overlay, "show", lambda *a: shown.append(a) or True)
    cid = (await S.CaptureScreen().run(S.CaptureArgs(), env)).evidence["capture"]["capture_id"]
    args = S.HighlightArgs(capture_id=cid, x=100, y=50, width=40, height=20, label="Click here")
    assert not S.HighlightArea().assess(args, env).error
    result = await S.HighlightArea().run(args, env)
    assert result.evidence["desktop_box"] == (200, 100, 80, 40)  # scale 2: image px -> desktop px
    assert shown[0][:6] == (200, 100, 80, 40, 2560, 1600)
    assert result.evidence["highlight"] == {"capture_id": cid, "x": 100, "y": 50, "width": 40, "height": 20,
                                            "label": "Click here"}  # fmt: skip


async def test_highlight_without_overlay_still_works_in_app(monkeypatch: pytest.MonkeyPatch) -> None:
    grab, _ = fake_grab((800, 600))
    env = T.HandsEnv(CONFIG, hands=FakeHands(), grab=grab)
    monkeypatch.setattr(S, "unavailable_reason", lambda: "no display")
    cid = (await S.CaptureScreen().run(S.CaptureArgs(), env)).evidence["capture"]["capture_id"]
    result = await S.HighlightArea().run(S.HighlightArgs(capture_id=cid, x=1, y=1, width=5, height=5), env)
    assert result.ok and "in the app" in result.output and result.evidence["on_screen"] is False


async def test_capture_refusals() -> None:
    grab, calls = fake_grab()
    blocked = FakeHands(windows=[fixture_window()], blocked_apps=("fixture",))
    env = T.HandsEnv(CONFIG, hands=blocked, grab=grab)
    assessment = S.CaptureScreen().assess(S.CaptureArgs(), env)
    assert assessment.error and "never capture" in assessment.error
    assert not S.CaptureScreen().assess(S.CaptureArgs(target="select"), env).error  # the user picks
    assert S.CaptureScreen().assess(S.CaptureArgs(target="window"), env).error == "window_id is required"
    highlight = S.HighlightArea().assess(S.HighlightArgs(capture_id="nope", x=0, y=0, width=1, height=1), env)
    assert highlight.error and "unknown screenshot" in highlight.error
    assert calls == []


async def test_capture_error_becomes_a_clear_result() -> None:
    def denied(kind: CaptureKind, bounds: tuple[int, int, int, int] | None) -> RawCapture:
        raise CaptureError("screenshots aren't allowed")

    env = T.HandsEnv(CONFIG, hands=FakeHands(), grab=denied)
    result = await S.CaptureScreen().run(S.CaptureArgs(), env)
    assert not result.ok and "aren't allowed" in result.output


def test_portal_file_is_deleted_after_reading(tmp_path: Path) -> None:
    shot = tmp_path / "Screenshot.png"
    Image.new("RGB", (10, 10), "red").save(shot)
    image = capture_mod._load_and_delete(shot.as_uri(), started=time.time())
    assert image.size == (10, 10) and not shot.exists()
    with pytest.raises(CaptureError):
        capture_mod._load_and_delete("https://example.com/x.png", started=time.time())


def test_wayland_window_capture_is_refused_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_mod, "session_kind", lambda: "wayland")
    with pytest.raises(CaptureError, match="on Wayland"):
        capture_mod.grab("window", (0, 0, 10, 10))


# ---- real capture + on-screen highlight in the private X11 desktop -----------------

REASON = missing_requirements() if sys.platform.startswith("linux") else "Linux only"
needs_desktop = pytest.mark.skipif(bool(REASON), reason=REASON or "")


@pytest.fixture(scope="module")
def desktop() -> Iterator[dict[str, str]]:
    if REASON:
        pytest.skip(REASON)
    with a11y_session() as env:
        yield env


@pytest.fixture
async def host(desktop: dict[str, str]) -> AsyncIterator[WorkerHost]:
    worker = WorkerHost("hands", "workers.hands", extra_env=desktop)
    await worker.start(CONFIG)
    yield worker
    await worker.stop()


@needs_desktop
async def test_real_capture_window_and_highlight_pixels(host: WorkerHost) -> None:
    screen = await host.invoke("capture_screen", {"target": "screen"})
    assert screen.ok and (screen.images[0].width, screen.images[0].height) == (1280, 800)
    windows = await host.invoke("list_windows", {})
    window_id = next(line.split()[0] for line in windows.output.splitlines() if "Hands Fixture" in line)
    window = await host.invoke("capture_screen", {"target": "window", "window_id": window_id})
    assert window.ok and (window.images[0].width, window.images[0].height) == (420, 320)

    cid = screen.evidence["capture"]["capture_id"]
    box = {"capture_id": cid, "x": 600, "y": 400, "width": 200, "height": 60, "label": "Click here"}
    highlight = await host.invoke("highlight_area", box)
    assert highlight.ok and highlight.evidence["on_screen"] is True
    await asyncio.sleep(0.6)
    after = decode((await host.invoke("capture_screen", {"target": "screen"})).images[0].data_b64)
    r, g, b = after.getpixel((700, 396))  # on the top bar
    assert r > 180 and g < 120 and b < 120, (r, g, b)
    inside = after.getpixel((700, 430))
    assert not (inside[0] > 180 and inside[1] < 120), "the box middle must stay clear for clicking"


@needs_desktop
async def test_blocked_app_on_screen_stops_capture(desktop: dict[str, str]) -> None:
    worker = WorkerHost("hands", "workers.hands", extra_env=desktop)
    await worker.start(CONFIG.model_copy(update={"blocked_apps": ("fixture",)}))
    try:
        assessment = await worker.assess("capture_screen", {"target": "screen"})
        assert assessment.error and "gtk_fixture_app" in assessment.error
        result = await worker.invoke("capture_screen", {"target": "screen"})
        assert not result.ok and not result.images
    finally:
        await worker.stop()
