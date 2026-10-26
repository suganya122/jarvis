"""Wires the hands-free loop to the rest of the daemon: the ears process, the speech worker,
the overlay, and the chat. Nothing here decides anything; see core/hands_free.py."""

import asyncio
import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from contracts.context import ActiveContext
from contracts.voice import SAMPLE_RATE, TranscriptEvent
from contracts.ws import ChatSend
from core import voice_install
from core.approvals import ApprovalBroker
from core.chat import ChatService
from core.chat_types import Emit
from core.config import Settings
from core.ears_host import EarsHost
from core.hands_free import HandsFree
from core.hands_free_types import HandsFreeDeps
from core.overlay import OverlayController
from core.store import Store
from core.worker_host import WorkerHost, WorkerUnavailable

log = logging.getLogger(__name__)
STREAM_PREFIX = "hf-"
WELCOMED = ".welcomed"
"""In the data folder once the first-start hello has been shown."""


def apple_speech_helper(engine: str) -> str:
    """macOS: Jarvis.app's executable, for Apple's on-device speech recognition ('' if not used)."""
    if sys.platform != "darwin" or engine == "vosk":
        return ""
    found = os.environ.get("JARVIS_SPEECH_HELPER") or str(
        Path.home() / "Applications" / "Jarvis.app" / "Contents" / "MacOS" / "Jarvis"
    )
    return found if Path(found).is_file() else ""


class _Recognizer:
    """The voice worker's streaming speech-to-text, for the hands-free loop's own streams."""

    def __init__(self, worker: Callable[[], WorkerHost | None]) -> None:
        self._worker = worker

    def _host(self) -> WorkerHost:
        host = self._worker()
        if host is None or not host.alive:
            raise WorkerUnavailable("the speech engine isn't running")
        return host

    async def open(self, stream_id: str) -> None:
        await self._host().audio_open(stream_id, SAMPLE_RATE)

    async def feed(self, stream_id: str, pcm_b64: str) -> None:
        await self._host().audio_chunk(stream_id, pcm_b64)

    async def close(self, stream_id: str, abort: bool = False) -> None:
        await self._host().audio_close(stream_id, abort=abort)


class VoiceLoop:
    def __init__(
        self,
        *,
        data_dir: Path,
        store: Store,
        chat: ChatService,
        overlay: OverlayController,
        approvals: ApprovalBroker,
        settings: Callable[[], Settings],
        paused: Callable[[], bool],
        voice_worker: Callable[[], WorkerHost | None],
        stt_model_ready: Callable[[], bool],
        begin_context: Callable[[], None],
        for_voice: Callable[[str], Awaitable[tuple[ActiveContext | None, str]]],
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._store = store
        self._chat = chat
        self._overlay = overlay
        self._for_voice = for_voice
        self._welcome: asyncio.Task[None] | None = None
        self._begin_context = begin_context
        self.ears = EarsHost(self._frame, extra_env, preferred_mic=lambda: settings().voice.microphone)
        self._mic_setting = settings().voice.microphone
        self._settings = settings

        def stt_ready() -> bool:
            worker = voice_worker()
            return stt_model_ready() and worker is not None and worker.alive

        self.loop = HandsFree(
            HandsFreeDeps(
                ears=self.ears,
                recognizer=_Recognizer(voice_worker),
                screen=overlay,
                settings=lambda: settings().voice,
                paused=paused,
                stt_ready=stt_ready,
                run_turn=self._run_turn,
                resolve_approval=lambda approval_id, ok: approvals.resolve(approval_id, ok, remember=False),
                on_wake=self._on_wake,
            )
        )

    def _on_wake(self) -> None:
        self._begin_context()  # what's on screen, as of the wake word
        self.refresh_voice()  # a natural voice installed since the last request

    def _frame(self, pcm_b64: str, level: float) -> None:
        self.loop.on_frame(pcm_b64, level)

    @staticmethod
    def owns(event: TranscriptEvent) -> bool:
        return event.stream_id.startswith(STREAM_PREFIX)

    def on_transcript(self, event: TranscriptEvent) -> None:
        self.loop.on_transcript(event)

    async def start(self) -> None:
        self.refresh_voice()
        await self.ears.start()
        await self.loop.start()
        marker = self._data_dir / WELCOMED
        if not marker.exists():
            marker.touch()
            self._welcome = asyncio.create_task(self._say_hello())

    async def _say_hello(self) -> None:
        """The first time it starts (right after installing): the box, a hello, a spoken "ready"."""
        for _ in range(40):  # let the overlay and the ears come up
            if self.loop.state != "off" or self.ears.available is not None:
                break
            await asyncio.sleep(0.25)
        talk = "hold ⌃⌥Space" if sys.platform == "darwin" else "use your voice shortcut"
        await self._overlay.welcome(f"Hi, I'm Jarvis. Say “Jarvis”, or {talk}, and tell me what to do.")
        await self.loop.say_now("Jarvis is ready.")

    def refresh_voice(self) -> None:
        """Pick up the natural voice if it has been installed since the last look."""
        self.ears.piper, self.ears.voice = voice_install.installed_paths(self._data_dir)

    async def stop(self) -> None:
        await self.loop.stop()
        await self.ears.stop()

    def interrupt(self) -> None:
        self.loop.interrupt()

    async def say(self, text: str) -> bool:
        """Speak on request (the page's Read aloud), the same way replies are spoken."""
        return await self.loop.say_now(text)

    def status(self) -> dict[str, str]:
        voice = "natural (Piper)" if self.ears.voice else "the computer's own voice"
        mic = f"{self.ears.microphone} ({self.ears.microphone_reason})" if self.ears.microphone else ""
        return {**self.loop.status(), "voice": voice, "microphone": mic}

    async def settings_changed(self) -> None:
        """A different microphone was chosen in Settings: reopen it."""
        wanted = self._settings().voice.microphone
        if wanted != self._mic_setting:
            self._mic_setting = wanted
            await self.loop.reopen_mic()

    async def _run_turn(self, text: str, session_id: str | None, emit: Emit) -> str:
        session = await self._store.ensure_session(session_id, text)
        active, text = await self._for_voice(text)  # what's on screen; "no, the other one" retargets
        request_id = uuid.uuid4().hex
        self._overlay.track(request_id, text)
        if active is not None:
            await self._overlay.set_target(active.label())
        await self._store.audit("user", "voice.hands_free", {"chars": len(text)})
        send = ChatSend(request_id=request_id, session_id=session, text=text)
        await self._chat.run(send, emit, None, active=active)
        return session
