"""Voice worker: streams audio into the speech engine and emits transcript events.

Audio for one stream is processed strictly in order on a background thread, so the
stdin reader never blocks and partials keep flowing while the user talks.
"""

import asyncio
import base64
import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

from pydantic import ValidationError

from contracts.voice import SpeechToText, TranscriptEvent
from contracts.worker import AudioChunk, AudioClose, AudioOpen, Transcript, WorkerConfig, to_worker_adapter
from workers.common.server import Server
from workers.voice.engine import SttError, VoskSpeechToText

WORKER_NAME = "voice"


@dataclass
class VoiceEnv:
    config: WorkerConfig
    stt: SpeechToText | None = None
    error: str | None = None
    queues: dict[str, asyncio.Queue[bytes | None]] = field(default_factory=dict)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)


def load_engine(config: WorkerConfig) -> VoiceEnv:
    env = VoiceEnv(config)
    try:
        env.stt = VoskSpeechToText(config.stt_model_dir)  # loaded now, so the first press is instant
    except SttError as exc:
        env.error = str(exc)
    except Exception as exc:  # a broken model folder must not kill the worker
        env.error = f"the speech model couldn't be loaded ({type(exc).__name__})"
    return env


class VoiceServer(Server):
    async def handle_line(self, line: str) -> None:
        try:
            msg = to_worker_adapter.validate_json(line)
        except ValidationError:
            print("voice worker: ignoring malformed message", file=sys.stderr)
            return
        if isinstance(msg, AudioOpen | AudioChunk | AudioClose):
            await self._audio(msg)
        else:
            await super().handle_line(line)

    def _emit(self, event: TranscriptEvent) -> None:
        self.write(Transcript(event=event))

    def _error(self, stream_id: str, message: str) -> None:
        self._emit(TranscriptEvent(stream_id=stream_id, kind="final", text="", ms=-1))
        print(f"voice worker: {message}", file=sys.stderr)

    async def _audio(self, msg: AudioOpen | AudioChunk | AudioClose) -> None:
        env: VoiceEnv | None = self._env
        if env is None:
            return
        if isinstance(msg, AudioOpen):
            if env.stt is None:
                self._emit(TranscriptEvent(stream_id=msg.id, kind="final", text="", ms=-1))
                return
            env.stt.open(msg.id, msg.sample_rate)
            queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            env.queues[msg.id] = queue
            task = asyncio.create_task(self._pump(env, msg.id, queue))
            env.tasks.add(task)
            task.add_done_callback(env.tasks.discard)
        elif isinstance(msg, AudioChunk):
            queue_ = env.queues.get(msg.id)
            if queue_ is not None:
                queue_.put_nowait(base64.b64decode(msg.pcm_b64))
        else:
            queue_ = env.queues.pop(msg.id, None)
            if queue_ is None:
                return
            if msg.abort and env.stt is not None:
                env.stt.abort(msg.id)
                queue_.put_nowait(b"")  # wake the pump so it exits
            queue_.put_nowait(None)

    async def _pump(self, env: VoiceEnv, stream_id: str, queue: asyncio.Queue[bytes | None]) -> None:
        stt = env.stt
        assert stt is not None
        try:
            while (chunk := await queue.get()) is not None:
                if not chunk:
                    return
                for event in await asyncio.to_thread(stt.feed, stream_id, chunk):
                    self._emit(event)
            self._emit(await asyncio.to_thread(stt.close, stream_id))
        except SttError as exc:
            stt.abort(stream_id)
            self._error(stream_id, str(exc))


def make_server(out: TextIO) -> Server:
    tools: dict[str, Any] = {}
    return VoiceServer(out, tools, load_engine)
