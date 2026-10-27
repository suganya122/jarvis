"""Runs the ears (workers/ears: microphone and speaker) as its own process.

Core sends it "mic on/off" and text to say; it sends back 100 ms microphone blocks and "done
speaking". It needs no token and no network. If there's no microphone it says so once and
can still speak.
"""

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

from contracts.ears import (
    EarsFrame,
    EarsListen,
    EarsMicChosen,
    EarsMicError,
    EarsReady,
    EarsSaid,
    EarsSay,
    EarsStopSaying,
    FromEars,
    Microphone,
    from_ears_adapter,
)
from core.line_process import LineProcess

log = logging.getLogger(__name__)
MAX_RESTARTS_PER_HOUR = 10
"""It restarts itself to recover (e.g. Jarvis.app's audio stopped); past this, something is broken."""
TEST_HOOKS = ("ASSISTANT_EARS_WAV", "ASSISTANT_EARS_SINK")
"""Tests replace the microphone with WAV files and record speech to a file instead of playing it.
Child processes don't get `ASSISTANT_*` variables, so these two are passed on by name, here, for
every caller: a test must never reach the real microphone or speakers."""


class EarsHost:
    def __init__(
        self,
        on_frame: Callable[[str, float], None],
        extra_env: dict[str, str] | None = None,
        on_problem: Callable[[str], None] | None = None,
        preferred_mic: Callable[[], str] = lambda: "",
    ) -> None:
        self._on_frame = on_frame
        self._preferred_mic = preferred_mic
        self._on_problem = on_problem or (lambda _detail: None)
        hooks = {k: os.environ[k] for k in TEST_HOOKS if k in os.environ}
        self._process = LineProcess("ears", "workers.ears", from_ears_adapter, self._dispatch, self._on_exit,
                                    extra_env={**hooks, **(extra_env or {})})  # fmt: skip
        self._saying: dict[str, asyncio.Future[EarsSaid]] = {}
        self._restarts: list[float] = []
        self._stopping = False
        self._want_mic = False
        self._tasks: set[asyncio.Task[object]] = set()
        self.available: bool | None = None  # None until the process reports
        self.detail = "starting"
        self.piper = ""
        self.voice = ""
        """Paths to the Piper program and voice model, when the natural voice is installed."""
        self.last_voice = "none"
        self.microphones: list[Microphone] = []
        self.microphone = ""
        """Name of the microphone in use, and why that one."""
        self.microphone_reason = ""

    async def start(self) -> None:
        self._stopping = False
        await self._process.start()
        if self._want_mic:  # after a restart
            await self._process.send(EarsListen(on=True, microphone=self._preferred_mic()))

    async def stop(self) -> None:
        self._stopping = True
        for future in self._saying.values():
            if not future.done():
                future.cancel()
        await self._process.stop()

    async def listen(self, on: bool) -> None:
        self._want_mic = on
        await self._process.send(EarsListen(on=on, microphone=self._preferred_mic() if on else ""))

    async def say(self, text: str, timeout_s: float = 90.0) -> bool:
        """Speak and wait until it's done. False if it was cut off or couldn't speak."""
        say_id = uuid.uuid4().hex
        future: asyncio.Future[EarsSaid] = asyncio.get_running_loop().create_future()
        self._saying[say_id] = future
        try:
            await self._process.send(
                EarsSay(say_id=say_id, text=text[:2000], piper=self.piper, voice=self.voice)
            )
            said = await asyncio.wait_for(future, timeout_s)
        except (TimeoutError, asyncio.CancelledError):
            await self._process.send(EarsStopSaying())
            raise
        finally:
            self._saying.pop(say_id, None)
        self.last_voice = said.voice
        if not said.ok and said.detail not in ("", "stopped"):
            log.warning("speaking failed: %s", said.detail)
        return said.ok

    async def stop_saying(self) -> None:
        await self._process.send(EarsStopSaying())

    def _dispatch(self, msg: FromEars) -> None:
        if isinstance(msg, EarsFrame):
            self._on_frame(msg.pcm_b64, msg.level)
        elif isinstance(msg, EarsSaid):
            future = self._saying.get(msg.say_id)
            if future is not None and not future.done():
                future.set_result(msg)
        elif isinstance(msg, EarsMicChosen):
            self.microphone, self.microphone_reason = msg.microphone.name, msg.reason
            self.detail = msg.microphone.name
            log.info("microphone: %s (%s)", msg.microphone.name, msg.reason)
        elif isinstance(msg, EarsReady):
            self.available, self.detail, self.microphones = msg.available, msg.detail, msg.microphones
            log.info("ears %s: %s", "ready" if msg.available else "can't listen", msg.detail)
        elif isinstance(msg, EarsMicError):
            self.available, self.detail = False, msg.detail
            log.warning("microphone problem: %s", msg.detail)
            self._on_problem(msg.detail)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_exit(self, code: int | None) -> None:
        for future in self._saying.values():
            if not future.done():
                future.cancel()
        if self._stopping:
            return
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < 3600]
        if len(self._restarts) >= MAX_RESTARTS_PER_HOUR:
            self.available, self.detail = False, f"the audio process kept stopping (exit code {code})"
            log.warning("ears disabled: it kept stopping")
            return
        self._restarts.append(now)
        log.warning("ears exited with code %s; restarting", code)
        self._spawn(self.start())
