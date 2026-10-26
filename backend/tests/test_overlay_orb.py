"""The overlay's orb and voice level: the loudness math, the throttled level updates,
reduced motion, the hint line, and the real orb reacting on a private Xvfb display."""

import asyncio
import base64
import json
import subprocess
import sys
from array import array

import pytest

from contracts.overlay import OverlayLevel, OverlayView, to_overlay_adapter
from contracts.ws import VoicePartial
from core.approvals import ApprovalBroker
from core.overlay import OverlayController, pcm_level
from tests.test_overlay_window import BACKEND, needs_x, xdisplay  # noqa: F401 - fixture
from workers.overlay.orb import blend, reduced_motion
from workers.overlay.window import hint


def pcm(value: int, n: int = 1600) -> str:
    return base64.b64encode(array("h", [value, -value] * (n // 2)).tobytes()).decode()


def test_loudness_is_zero_for_silence_and_rises_with_the_voice() -> None:
    assert pcm_level(pcm(0)) == 0.0
    assert pcm_level(pcm(20)) == 0.0  # below -60 dBFS: room noise
    quiet, loud = pcm_level(pcm(1000)), pcm_level(pcm(16000))
    assert 0.2 < quiet < loud < 1.0
    assert pcm_level(pcm(32767)) == pytest.approx(1.0, abs=0.01)
    assert pcm_level("not base64!") == 0.0
    assert pcm_level("") == 0.0


class LevelSurface:
    def __init__(self) -> None:
        self.levels: list[float] = []

    async def show(self, view: OverlayView) -> None:
        pass

    async def hide(self) -> None:
        pass

    async def level(self, value: float) -> None:
        self.levels.append(value)


def test_levels_go_to_the_overlay_only_while_listening_and_throttled() -> None:
    async def run() -> list[float]:
        async def stop_all() -> None:
            pass

        ctl = OverlayController(ApprovalBroker(), stop_all, lambda: True)
        surface = LevelSurface()
        ctl.surface = surface
        await ctl.audio(pcm(8000))  # nothing showing: no level
        await ctl.observe(VoicePartial(stream_id="s", text="hello"))
        for _ in range(20):  # a burst arrives at once: one update gets through
            await ctl.audio(pcm(8000))
        await asyncio.sleep(0.1)
        await ctl.audio(pcm(8000))
        return surface.levels

    levels = asyncio.run(run())
    assert len(levels) == 2 and all(0 < v < 1 for v in levels)


def test_the_level_message_carries_only_a_number() -> None:
    msg = to_overlay_adapter.validate_json(OverlayLevel(level=0.5).model_dump_json())
    assert msg == OverlayLevel(level=0.5)
    assert set(json.loads(msg.model_dump_json())) == {"type", "level"}
    with pytest.raises(ValueError):
        OverlayLevel(level=1.5)


def test_colour_blending_and_reduced_motion_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert blend("#000000", "#ffffff", 0.5) == "#808080"
    assert blend("#6ee7b7", "#141416", 0) == "#6ee7b7"
    assert blend("#6ee7b7", "#141416", 2) == "#141416"
    monkeypatch.setenv("ASSISTANT_REDUCED_MOTION", "1")
    assert reduced_motion()
    monkeypatch.setenv("ASSISTANT_REDUCED_MOTION", "0")
    assert not reduced_motion()


def test_the_hint_says_what_to_do_next() -> None:
    assert "again to run it" in hint(OverlayView(phase="listening"))
    assert hint(OverlayView(phase="listening", auto_send=True)).startswith("Stop talking to run it")
    assert hint(OverlayView(phase="done")) == ""
    assert "browser" in hint(OverlayView(phase="needs_chat"))


ORB_PROBE = """
import json, time, tkinter as tk
from workers.overlay.orb import Orb, RADIUS
root = tk.Tk()
orb = Orb(root, "#141416", still={still})
orb.canvas.pack(); orb.set("listening", "#6ee7b7"); orb.start()
def sample():
    root.update(); time.sleep(0.04); root.update()
    return orb.radius(time.monotonic()), len(orb.canvas.find_all())
quiet = [sample() for _ in range(5)]
orb.level(0.9)
loud = [sample() for _ in range(15)]
orb.stop()
print(json.dumps({{"base": RADIUS, "quiet": max(r for r, _ in quiet), "loud": max(r for r, _ in loud),
                  "items": max(n for _, n in loud)}}))
"""


def _probe(env: dict[str, str], still: bool) -> dict[str, float]:
    out = subprocess.run([sys.executable, "-c", ORB_PROBE.format(still=still)], env=env, cwd=BACKEND,
                         capture_output=True, text=True, timeout=30, check=True)  # fmt: skip
    return json.loads(out.stdout)  # type: ignore[no-any-return]


@needs_x
def test_the_orb_swells_and_rings_with_the_voice(xdisplay: dict[str, str]) -> None:  # noqa: F811
    moving = _probe(xdisplay, still=False)
    assert moving["loud"] > moving["quiet"] * 1.2  # swells with the voice
    assert moving["items"] > 2  # halo + orb + at least one ring
    still = _probe(xdisplay, still=True)
    assert still["loud"] == still["quiet"] == still["base"]  # reduced motion: a still dot
    assert still["items"] == 2
