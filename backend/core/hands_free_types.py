"""What the hands-free loop (core/hands_free.py) needs from the rest of the daemon, and its
timing constants. Protocols, so tests can stand in fake ears, a fake recogniser and a fake chat."""

import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from contracts.ws import ServerMessage
from core.config import VoiceSettings

HandsFreeState = Literal["off", "idle", "listening", "thinking", "speaking", "approval"]
POLL_S = 2.0
TICK_S = 0.25
QUIET_LEVEL = 0.15
"""Mic level (0-1, log scale) below which it always counts as quiet (about -51 dBFS); in a noisier
room the learned background (NoiseFloor) raises it. Kept low: a MacBook's built-in microphone
hears ordinary speech at only about 0.1-0.3."""
STALLED_EXTRA_S = 1.0
"""Noisy rooms never go quiet: this long after the words stop changing also ends a request."""
ROTATE_S = 60.0
GATE_LEVEL = 0.06
"""While only waiting for the wake word, audio below this (about -58 dBFS) isn't sent to the speech
engine at all, so a quiet room costs next to no CPU. Half a second before the sound goes with it.
Once addressed (live, follow-ups, yes/no) everything is sent."""
GATE_HANG_S = 1.0
PREROLL_FRAMES = 5
QUIET_FOR_S = 0.8
SPEAK_TAIL_S = 0.5
SESSION_TTL_S = 600.0
ECHO_WINDOW_S = 4.0
"""For this long after it stops speaking, its own words may still reach the microphone."""
ECHO_CHARS = 1200
ENGINE_RETRY_S = 30.0
"""After the speech engine reports a problem (e.g. Dictation is off), try it again this often."""
SPECULATE_PAUSE_S = 0.7
"""A pause this long, mid-request, starts the model early (see HandsFreeDeps.speculate)."""
FINAL_WAIT_S = 3.0
APPROVAL_LISTEN_S = 10.0
APPROVAL_SILENCE_S = 1.2
ACK = "Yes?"
END_ACK = "Okay."
OFFLINE_ACK = "Going offline. Say Hey Jarvis when you need me."
Emit = Callable[[ServerMessage], Awaitable[None]]


class Ears(Protocol):
    available: bool | None
    detail: str

    async def listen(self, on: bool) -> None: ...

    async def say(self, text: str, timeout_s: float = ...) -> bool: ...

    async def stop_saying(self) -> None: ...


class Recognizer(Protocol):
    async def open(self, stream_id: str) -> None: ...

    async def feed(self, stream_id: str, pcm_b64: str) -> None: ...

    async def close(self, stream_id: str, abort: bool = False) -> None: ...


class Screen(Protocol):
    """The overlay window."""

    async def voice_show(
        self, phase: str, partial: str = "", status: str = "", hide_after: float | None = None
    ) -> None: ...

    async def voice_hide(self) -> None: ...

    def voice_begin(self) -> None: ...

    async def voice_rest(self) -> None: ...

    async def level(self, value: float) -> None: ...

    async def observe(self, msg: ServerMessage) -> None: ...


@dataclass
class HandsFreeDeps:
    ears: Ears
    recognizer: Recognizer
    screen: Screen
    settings: Callable[[], VoiceSettings]
    paused: Callable[[], bool]
    stt_ready: Callable[[], bool]
    run_turn: Callable[[str, str | None, Emit, object | None], Coroutine[Any, Any, str]]
    """(text, session id or None, emit, speculation) -> the session id used. Runs one chat turn."""
    resolve_approval: Callable[[str, bool], bool]
    on_wake: Callable[[], None] = lambda: None
    speculate: Callable[[str, str | None], Coroutine[Any, Any, object | None]] | None = None
    """Start the model on what was said so far, during a pause (its tools wait for the final words)."""
    discard: Callable[[object], Awaitable[None]] | None = None
    clock: Callable[[], float] = time.monotonic


class NoiseFloor:
    """The room's own background level, learned while listening, so "quiet" means quiet for this
    microphone: a webcam in a still room can sit at 0.3 where a headset sits at 0.05.

    It's the quietest fifth of the last ten seconds: the pauses between words always land there,
    so even a long sentence never becomes "background"."""

    SPEECH_MARGIN = 0.12
    """This far above the background counts as sound (a voice)."""
    GATE_MARGIN = 0.08
    """This far above it, audio is worth sending to the speech engine while waiting for the wake word."""
    WINDOW = 100
    """Frames (10 s of 100 ms blocks)."""
    TRUST_AFTER = 30
    """Until 3 s have been heard the starting guess stands (lower if it's quieter), so speech
    heard first, right as the microphone opens, isn't taken for the room."""

    def __init__(self, start: float = 0.15) -> None:
        self._recent: deque[float] = deque(maxlen=self.WINDOW)
        self._start = start
        self.value = start

    def update(self, level: float) -> None:
        self._recent.append(level)
        ordered = sorted(self._recent)
        if len(ordered) < self.TRUST_AFTER:
            self.value = min(self._start, ordered[0])
        else:
            self.value = ordered[len(ordered) // 5]

    def sound(self) -> float:
        return max(QUIET_LEVEL, self.value + self.SPEECH_MARGIN)

    def gate(self) -> float:
        return max(GATE_LEVEL, self.value + self.GATE_MARGIN)
