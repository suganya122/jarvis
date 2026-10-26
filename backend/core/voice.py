"""Voice streams: browser microphone -> voice worker -> partial/final transcripts.

One stream per connection at a time. A pause (segment) in chat mode may start a
speculative model call; the final transcript decides whether it's used.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from contracts.model import ModelTier
from contracts.voice import TranscriptEvent
from contracts.ws import VoiceAudio, VoiceError, VoiceFinal, VoiceMode, VoicePartial, VoiceStart, VoiceStop
from core.chat import ChatService
from core.chat_types import Emit
from core.speculation import Speculation, normalize
from core.worker_host import WorkerHost, WorkerUnavailable

log = logging.getLogger(__name__)
PENDING_TTL_S = 60.0
NO_MODEL = "Voice needs a speech model first: download one in Settings → Voice."

Dictate = Callable[[str, str, VoiceMode, Emit], Awaitable[None]]


@dataclass
class _Stream:
    stream_id: str
    mode: VoiceMode
    session_id: str | None
    tier: ModelTier | None
    emit: Emit
    released: float | None = None
    speculation: Speculation | None = None


class VoiceHub:
    def __init__(
        self,
        worker: Callable[[], WorkerHost | None],
        chat: ChatService,
        dictate: Dictate,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._worker = worker
        self._chat = chat
        self._dictate = dictate
        self._clock = clock
        self._streams: dict[str, _Stream] = {}
        self._pending: dict[str, tuple[Speculation, float]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start(self, emit: Emit, msg: VoiceStart) -> None:
        worker = self._worker()
        if worker is None or not worker.alive:
            await emit(VoiceError(stream_id=msg.stream_id, message=NO_MODEL))
            return
        for old in [s for s in self._streams.values() if s.emit is emit]:  # one stream per connection
            await self._abort(old)
        self._streams[msg.stream_id] = _Stream(msg.stream_id, msg.mode, msg.session_id, msg.tier, emit)
        try:
            await worker.audio_open(msg.stream_id, msg.sample_rate)
        except WorkerUnavailable:
            self._streams.pop(msg.stream_id, None)
            await emit(VoiceError(stream_id=msg.stream_id, message="The voice engine isn't running."))

    async def audio(self, msg: VoiceAudio) -> None:
        worker = self._worker()
        if msg.stream_id in self._streams and worker is not None:
            try:
                await worker.audio_chunk(msg.stream_id, msg.pcm_b64)
            except WorkerUnavailable:
                await self._abort(self._streams[msg.stream_id])

    async def stop(self, msg: VoiceStop) -> None:
        stream = self._streams.get(msg.stream_id)
        if stream is None:
            return
        if msg.cancel:
            await self._abort(stream)
            return
        stream.released = self._clock()
        worker = self._worker()
        if worker is not None:
            await worker.audio_close(msg.stream_id)

    async def close_connection(self, emit: Emit) -> None:
        for stream in [s for s in self._streams.values() if s.emit is emit]:
            await self._abort(stream)

    async def _abort(self, stream: _Stream) -> None:
        self._streams.pop(stream.stream_id, None)
        if stream.speculation is not None:
            await self._chat.discard(stream.speculation)
        worker = self._worker()
        if worker is not None:
            with contextlib.suppress(WorkerUnavailable):
                await worker.audio_close(stream.stream_id, abort=True)

    def take_speculation(self, stream_id: str) -> Speculation | None:
        found = self._pending.pop(stream_id, None)
        return found[0] if found else None

    def on_transcript(self, event: TranscriptEvent) -> None:
        """Called on the event loop by the worker host for every transcript event."""
        self._spawn(self._handle(event))

    async def _handle(self, event: TranscriptEvent) -> None:
        stream = self._streams.get(event.stream_id)
        if stream is None:
            return
        if event.kind != "final":
            await stream.emit(VoicePartial(stream_id=event.stream_id, text=event.text))
            if event.kind == "segment" and stream.mode == "chat" and event.text:
                await self._respeculate(stream, event.text)
            return
        self._streams.pop(event.stream_id, None)
        if event.ms < 0:
            await stream.emit(VoiceError(stream_id=event.stream_id, message=NO_MODEL))
            return
        released = stream.released or self._clock()
        release_ms = round((self._clock() - released) * 1000, 1)
        await stream.emit(VoiceFinal(stream_id=event.stream_id, text=event.text, mode=stream.mode,
                                     release_to_final_ms=release_ms))  # fmt: skip
        log.info("voice stream=%s mode=%s release_to_final_ms=%.1f", event.stream_id, stream.mode, release_ms)
        spec, stream.speculation = stream.speculation, None
        if stream.mode == "chat" and spec is not None and spec.text == normalize(event.text):
            self._pending[event.stream_id] = (spec, self._clock())  # the next chat.send can adopt it
            self._spawn(self._expire(event.stream_id))
        elif spec is not None:
            await self._chat.discard(spec)
        if stream.mode != "chat" and event.text:
            await self._dictate(event.stream_id, event.text, stream.mode, stream.emit)

    async def _respeculate(self, stream: _Stream, text: str) -> None:
        if stream.speculation is not None:
            if stream.speculation.text == normalize(text):
                return
            await self._chat.discard(stream.speculation)
            stream.speculation = None
        stream.speculation = await self._chat.speculate(stream.session_id, text, stream.tier)

    async def _expire(self, stream_id: str) -> None:
        await asyncio.sleep(PENDING_TTL_S)
        found = self._pending.pop(stream_id, None)
        if found is not None:
            await self._chat.discard(found[0])
