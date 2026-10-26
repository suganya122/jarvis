"""WebSocket chat endpoint. Auth and Origin are enforced earlier by SecurityMiddleware."""

import asyncio
import contextlib
import logging

from pydantic import ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect

from contracts import APP_NAME, APP_VERSION, CONTRACT_VERSION
from contracts.ws import (
    MAX_CHAT_TEXT,
    WS_SUBPROTOCOL,
    ApprovalRespond,
    ChatCancel,
    ChatError,
    ChatSend,
    ControlState,
    Hello,
    Ping,
    Pong,
    ServerMessage,
    SetPaused,
    StopAll,
    TaskControl,
    VoiceAudio,
    VoiceStart,
    VoiceStop,
    client_message_adapter,
)
from core.ratelimit import TokenBucket
from core.runtime import Runtime

log = logging.getLogger(__name__)
MAX_FRAME_CHARS = MAX_CHAT_TEXT * 2


async def chat_socket(ws: WebSocket) -> None:
    runtime: Runtime = ws.app.state.runtime
    offered = ws.scope.get("subprotocols", [])
    await ws.accept(subprotocol=WS_SUBPROTOCOL if WS_SUBPROTOCOL in offered else None)
    session = _Connection(ws, runtime)
    await session.serve()


class _Connection:
    def __init__(self, ws: WebSocket, runtime: Runtime) -> None:
        self._ws = ws
        self._rt = runtime
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sends = TokenBucket(runtime.settings.server.chat_sends_per_minute)

    async def emit(self, message: ServerMessage) -> None:
        async with self._lock:
            # If the client went away the turn still finishes and is persisted.
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self._ws.send_text(message.model_dump_json())
        await self._rt.overlay.observe(message)  # the overlay mirrors voice streams and voice turns

    async def serve(self) -> None:
        await self.emit(Hello(app_name=APP_NAME, app_version=APP_VERSION, contract_version=CONTRACT_VERSION))
        await self.emit(ControlState(paused=self._rt.paused))
        self._rt.add_listener(self.emit)
        try:
            while True:
                raw = await self._ws.receive_text()
                await self._handle(raw)
        except WebSocketDisconnect:
            pass
        finally:
            self._rt.remove_listener(self.emit)
            await self._rt.voice.close_connection(self.emit)
            for task in self._tasks.values():
                task.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _handle(self, raw: str) -> None:
        if len(raw) > MAX_FRAME_CHARS:
            await self.emit(ChatError(request_id=None, code="invalid_message", message="Message too large."))
            return
        try:
            msg = client_message_adapter.validate_json(raw)
        except ValidationError:
            await self.emit(ChatError(request_id=None, code="invalid_message", message="Malformed message."))
            return
        if isinstance(msg, Ping):
            await self.emit(Pong())
        elif isinstance(msg, ChatCancel):
            task = self._tasks.get(msg.request_id)
            if task is not None:
                task.cancel()
        elif isinstance(msg, ApprovalRespond):
            self._rt.approvals.resolve(msg.approval_id, msg.approved, msg.remember)
        elif isinstance(msg, StopAll):
            await self._rt.stop_all()
        elif isinstance(msg, SetPaused):
            await self._rt.set_paused(msg.paused)
        elif isinstance(msg, ChatSend):
            await self._start(msg)
        elif isinstance(msg, TaskControl):
            await self._task_control(msg)
        elif isinstance(msg, VoiceStart):
            await self._rt.voice.start(self.emit, msg)
        elif isinstance(msg, VoiceAudio):
            await self._rt.voice.audio(msg)
            await self._rt.overlay.audio(msg.pcm_b64)
        elif isinstance(msg, VoiceStop):
            await self._rt.voice.stop(msg)

    async def _start(self, msg: ChatSend) -> None:
        if self._rt.paused:
            await self.emit(
                ChatError(
                    request_id=msg.request_id, code="paused", message="Paused. Resume to send messages."
                )
            )
            return
        limit = self._rt.settings.server.max_concurrent_chats
        if msg.request_id in self._tasks or len(self._tasks) >= limit:
            await self.emit(
                ChatError(request_id=msg.request_id, code="busy", message="Too many requests at once.")
            )
            return
        if not self._sends.allow():
            await self.emit(
                ChatError(request_id=msg.request_id, code="rate_limited_local", message="Slow down a little.")
            )
            return
        if msg.background:  # detached: runs on without this tab; progress arrives as task.update
            await self._rt.background.submit(msg.text, session_id=msg.session_id, tier=msg.tier)
            return
        if msg.voice is not None:
            self._rt.overlay.track(msg.request_id, msg.text)
        task = asyncio.create_task(self._run(msg))
        self._tasks[msg.request_id] = task
        self._rt.track(task)
        task.add_done_callback(lambda _t: self._tasks.pop(msg.request_id, None))

    async def _task_control(self, msg: TaskControl) -> None:
        bg = self._rt.background
        done = await {"cancel": bg.cancel, "resume": bg.resume, "discard": bg.discard}[msg.action](
            msg.task_id
        )
        if done:
            await self._rt.store.audit("user", f"task.{msg.action}", {"task": msg.task_id})

    async def _run(self, msg: ChatSend) -> None:
        try:
            speculation = self._rt.voice.take_speculation(msg.voice.stream_id) if msg.voice else None
            active = None
            if msg.voice is not None:  # what the user was looking at when they pressed the hotkey
                active, text = await self._rt.context.for_voice(msg.text)
                if text != msg.text:  # "no, the other one": the last request, on the corrected target
                    msg, speculation = msg.model_copy(update={"text": text}), None
                if active is not None:
                    await self._rt.overlay.set_target(active.label())
            await self._rt.chat.run(msg, self.emit, speculation, active=active)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("chat turn failed")
            await self.emit(
                ChatError(request_id=msg.request_id, code="provider_error", message="Internal error.")
            )
