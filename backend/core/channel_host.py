"""Runs a channel plugin as a separate process and adapts it to the `Channel` interface.

Like workers: `python -m <entrypoint>`, one JSON message per line, an environment with
secrets stripped. The plugin's own secrets (e.g. a bot token) go over stdin in
`channel.configure`, never through the environment or the command line.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable

from contracts.channel import (
    ApprovalDecision,
    ApprovalRequest,
    ChannelAskApproval,
    ChannelCloseApproval,
    ChannelConfigure,
    ChannelInbound,
    ChannelReady,
    ChannelSend,
    ChannelState,
    ChannelStatus,
    ChannelTyping,
    FromChannel,
    InboundMessage,
    OutboundMessage,
    ToChannel,
    from_channel_adapter,
)
from core.line_process import LineProcess
from core.plugins import Plugin, compatible

log = logging.getLogger(__name__)
MAX_RESTARTS_PER_HOUR = 5
StatusCallback = Callable[[str, ChannelState, str], None]


class ChannelHost:
    def __init__(
        self,
        plugin: Plugin,
        options: dict[str, str],
        secrets: dict[str, str],
        on_status: StatusCallback,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.name = plugin.name
        self._configure = ChannelConfigure(options=options, secrets=secrets)
        self._on_status = on_status
        self._process = LineProcess(
            plugin.name,
            plugin.manifest.entrypoint,
            from_channel_adapter,
            self._dispatch,
            self._on_exit,
            pythonpath=(plugin.pythonpath,),
            cwd=plugin.folder,
            extra_env=extra_env,
        )
        self._inbox: asyncio.Queue[InboundMessage | ApprovalDecision] = asyncio.Queue(maxsize=200)
        self._restarts: list[float] = []
        self._stopping = False
        self.state: ChannelState = "stopped"
        self.detail = ""

    # ---- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._stopping = False
        self._set_status("starting", "")
        await self._process.start()
        await self._send(self._configure)

    async def stop(self) -> None:
        self._stopping = True
        await self._process.stop()
        self._set_status("stopped", "")

    @property
    def alive(self) -> bool:
        return self._process.alive

    # ---- Channel -------------------------------------------------------------

    async def receive(self) -> AsyncIterator[InboundMessage | ApprovalDecision]:
        while True:
            yield await self._inbox.get()

    async def send(self, message: OutboundMessage) -> None:
        await self._send(ChannelSend(conversation_id=message.conversation_id, text=message.text))

    async def request_approval(self, request: ApprovalRequest) -> None:
        await self._send(ChannelAskApproval(request=request))

    async def close_approval(self, approval_id: str, conversation_id: str, outcome: str) -> None:
        await self._send(
            ChannelCloseApproval(approval_id=approval_id, conversation_id=conversation_id, outcome=outcome)
        )

    async def typing(self, conversation_id: str) -> None:
        await self._send(ChannelTyping(conversation_id=conversation_id))

    # ---- internals -----------------------------------------------------------

    def _set_status(self, state: ChannelState, detail: str) -> None:
        self.state, self.detail = state, detail
        self._on_status(self.name, state, detail)

    async def _send(self, message: ToChannel) -> None:
        if not await self._process.send(message):
            log.warning("%s channel is not running; dropped %s", self.name, message.type)

    def _dispatch(self, msg: FromChannel) -> None:
        if isinstance(msg, ChannelReady):
            if not compatible(msg.contract_version):
                self._set_status("error", f"written for contracts {msg.contract_version}")
            return
        if isinstance(msg, ChannelStatus):
            self._set_status(msg.state, msg.detail[:300])
            return
        item: InboundMessage | ApprovalDecision
        if isinstance(msg, ChannelInbound):
            item = msg.message.model_copy(update={"channel": self.name})  # a plugin can't speak for another
        else:
            item = msg.decision
        try:
            self._inbox.put_nowait(item)
        except asyncio.QueueFull:
            log.warning("%s channel is flooding; dropping a message", self.name)

    def _on_exit(self, code: int | None) -> None:
        if self._stopping:
            return
        log.warning("%s channel exited with code %s", self.name, code)
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < 3600]
        if len(self._restarts) >= MAX_RESTARTS_PER_HOUR:
            self._set_status("error", "it kept stopping, so it was turned off for now")
            return
        self._restarts.append(now)
        self._set_status("error", "stopped unexpectedly; restarting")
        asyncio.get_running_loop().create_task(self._restart())

    async def _restart(self) -> None:
        await asyncio.sleep(2)
        if not self._stopping:
            await self.start()
