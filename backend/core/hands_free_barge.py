"""Listening while it thinks and talks, so the person can cut in: "wait", "stop", or "no, the other
screen" stops the reply at once, the way talking over a person does.

Its own voice reaches the microphone too, so what the speech engine hears is compared with what
is being said (speech_text.is_barge_in). The sound since the person started is kept, so the next
request starts from their first word, not from where the cut-in was noticed."""

import asyncio
import contextlib
import logging
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from contracts.voice import TranscriptEvent
from core.speech_text import is_barge_in

log = logging.getLogger(__name__)
LEAD_IN_FRAMES = 30
"""The last three seconds of sound, handed to the next request when someone cuts in."""
ROTATE_FRAMES = 600
"""A fresh stream every minute: speech engines limit how long one can run."""


class BargeIn:
    def __init__(
        self,
        recognizer: Any,
        *,
        saying: Callable[[], str],
        wake_word: str,
        paused: Callable[[], bool],
        on_cut_in: Callable[[], None],
    ) -> None:
        self._rec = recognizer
        self._saying = saying
        self._wake_word = wake_word
        self._paused = paused
        self._on_cut_in = on_cut_in
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.stream = ""
        self.frames: deque[str] = deque(maxlen=LEAD_IN_FRAMES)
        self.cut_in = False
        self._heard = {"frames": 0, "transcripts": 0, "words": 0}  # counts for the log, never words

    def frame(self, pcm_b64: str) -> None:
        self._queue.put_nowait(("frame", pcm_b64))

    def transcript(self, event: TranscriptEvent) -> None:
        if event.stream_id == self.stream:
            self._queue.put_nowait(("stt", event))

    async def run(self) -> None:
        """Until cancelled, or until someone cuts in (then `cut_in` is set and `frames` holds them)."""
        fed = 0
        try:
            while True:
                if not self.stream or fed >= ROTATE_FRAMES:
                    await self._close()
                    self.stream, fed = f"bi-{uuid.uuid4().hex[:12]}", 0
                    await self._rec.open(self.stream)
                kind, data = await self._queue.get()
                if self._paused():  # a spoken yes/no is being asked for: that has its own ears
                    continue
                if kind == "frame":
                    self.frames.append(data)
                    await self._rec.feed(self.stream, data)
                    fed += 1
                    self._heard["frames"] += 1
                    continue
                self._heard["transcripts"] += 1
                self._heard["words"] = max(self._heard["words"], len(data.text.split()))
                if data.error:
                    log.warning("cut-in listener: the speech engine stopped (%s)", data.error)
                    return  # the main loop reports it
                elif is_barge_in(data.text, self._saying(), self._wake_word):
                    self.cut_in = True
                    self._on_cut_in()
                    return
        finally:
            await self._close()
            h = self._heard
            log.info(
                "cut-in listener: %.1f s of sound, %d transcripts (up to %d words)%s",
                h["frames"] / 10,
                h["transcripts"],
                h["words"],
                ", cut in" if self.cut_in else "",
            )

    async def _close(self) -> None:
        stream, self.stream = self.stream, ""
        if stream:
            with contextlib.suppress(Exception):  # the engine may already be gone
                await self._rec.close(stream, abort=True)
