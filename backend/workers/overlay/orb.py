"""The overlay's orb: breathes while idle, swells with the voice while listening (with soft
rings), pulses while working. With reduced motion it's a still dot that only changes colour.

Tk has no alpha on canvas items, so "soft" means colours blended toward the background.
"""

import contextlib
import math
import os
import subprocess
import sys
import time
import tkinter as tk

SIZE = 64
RADIUS = 14
FRAME_MS = 33
BREATH_S = 3.2
PULSE_S = 1.2
RING_EVERY_S = 0.45
RING_LIFE_S = 1.1
RING_GROWTH = 16
SPEAKING = 0.12
"""Above this level the user is speaking: rings start."""


def blend(color: str, toward: str, amount: float) -> str:
    """`color` moved `amount` (0-1) of the way to `toward`, as #rrggbb."""
    a = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(toward[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = (round(x + (y - x) * min(1.0, max(0.0, amount))) for x, y in zip(a, b, strict=True))
    return "#" + "".join(f"{c:02x}" for c in mixed)


def reduced_motion() -> bool:
    """The OS "reduce motion / animations off" setting; unknown means motion is fine."""
    forced = os.environ.get("ASSISTANT_REDUCED_MOTION")
    if forced is not None:
        return forced not in ("", "0")
    with contextlib.suppress(Exception):
        if sys.platform == "win32":
            import ctypes

            enabled = ctypes.c_int(1)
            spi_getclientareaanimation = 0x1042
            ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined,unused-ignore]
                spi_getclientareaanimation, 0, ctypes.byref(enabled), 0
            )
            return not enabled.value
        if sys.platform == "darwin":
            out = _read(["defaults", "read", "com.apple.universalaccess", "reduceMotion"])
            return out == "1"
        out = _read(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"])
        return out == "false"
    return False


def _read(cmd: list[str]) -> str:
    # Fixed argument lists for the OS's own settings tools; nothing from outside.
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, check=False)  # noqa: S603
    return done.stdout.strip() if done.returncode == 0 else ""


class Orb:
    def __init__(
        self, parent: tk.Misc, background: str, still: bool, size: int = SIZE, radius: float = RADIUS
    ) -> None:
        self._size, self._radius, self._growth = size, radius, RING_GROWTH * size / SIZE
        self.canvas = tk.Canvas(parent, width=size, height=size, bg=background, highlightthickness=0, bd=0)
        self._bg = background
        self._still = still
        self._color = "#6ee7b7"
        self._mode = "idle"  # idle | listening | working | still
        self._target = 0.0
        self._level = 0.0
        self._rings: list[float] = []
        self._last_ring = 0.0
        self._job: str | None = None
        self._draw(time.monotonic())

    def set(self, mode: str, color: str) -> None:
        self._mode, self._color = mode, color
        if mode != "listening":
            self._target = 0.0
        self._draw(time.monotonic())

    def level(self, value: float) -> None:
        self._target = value

    def start(self) -> None:
        if self._job is None and not self._still:
            self._tick()

    def stop(self) -> None:
        if self._job is not None:
            with contextlib.suppress(tk.TclError):
                self.canvas.after_cancel(self._job)
            self._job = None
        self._rings.clear()
        self._level = self._target = 0.0

    def _tick(self) -> None:
        now = time.monotonic()
        # Fast attack, slow release: the orb jumps with a syllable and settles gently.
        rate = 0.55 if self._target > self._level else 0.12
        self._level += (self._target - self._level) * rate
        if self._mode == "listening" and self._level > SPEAKING and now - self._last_ring >= RING_EVERY_S:
            self._rings.append(now)
            self._last_ring = now
        self._rings = [born for born in self._rings if now - born < RING_LIFE_S]
        self._draw(now)
        self._job = self.canvas.after(FRAME_MS, self._tick)

    def radius(self, now: float) -> float:
        if self._still or self._mode == "still":
            return self._radius
        if self._mode == "listening":
            breath = 0.04 * math.sin(2 * math.pi * now / BREATH_S)
            return self._radius * (1 + breath + 0.45 * self._level)
        period = PULSE_S if self._mode == "working" else BREATH_S
        return self._radius * (1 + 0.07 * math.sin(2 * math.pi * now / period))

    def _draw(self, now: float) -> None:
        c, mid = self.canvas, self._size / 2
        c.delete("all")
        for born in self._rings:
            age = (now - born) / RING_LIFE_S
            r = self._radius + 4 + self._growth * age
            ring = blend(self._color, self._bg, 0.35 + 0.65 * age)
            c.create_oval(mid - r, mid - r, mid + r, mid + r, outline=ring, width=1.5)
        r = self.radius(now)
        halo = r + 5
        glow = blend(self._color, self._bg, 0.82)
        c.create_oval(mid - halo, mid - halo, mid + halo, mid + halo, fill=glow, width=0)
        c.create_oval(mid - r, mid - r, mid + r, mid + r, fill=self._color, width=0)
