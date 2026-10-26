"""Conversations from chat apps: who may talk, which chat it continues, and how a turn's
events become channel messages.

Rules:
- Only **paired** accounts are served. Pairing needs a one-time code shown in the app
  (6 digits, 10 minutes, 5 tries); everyone else gets one short "this is private" reply
  per hour and is otherwise ignored.
- Each conversation continues its own chat session, marked `remote` for the policy gate:
  anything beyond reading asks, every time, and approvals come back as buttons.
- One turn at a time per conversation; /stop cancels it, /new starts a fresh chat.
"""

import asyncio
import contextlib
import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import aiosqlite

from contracts.channel import ApprovalDecision, ApprovalRequest, Channel, InboundMessage, OutboundMessage
from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatCancelled,
    ChatDelta,
    ChatDone,
    ChatError,
    ChatSend,
    ServerMessage,
    ToolStarted,
)
from core.approvals import ApprovalBroker
from core.chat import ChatService
from core.config import PairedAccount, Settings
from core.ratelimit import KeyedLimiter
from core.store import Store

log = logging.getLogger(__name__)
CODE_TTL_S = 600
CODE_TRIES = 5
PRIVATE_REPLY_EVERY_S = 3600
HELP = (
    "Send me a message and I'll work on it on your computer. Anything that changes something asks "
    "you first, with Approve / Deny buttons.\n/new starts a fresh chat, /stop cancels what I'm doing."
)


@dataclass
class _Code:
    code: str
    expires_at: float
    tries: int = 0


@dataclass
class _Pending:
    channel: str
    conversation_id: str
    user_id: str


@dataclass
class ChannelDeps:
    store: Store
    chat: ChatService
    approvals: ApprovalBroker
    settings: Callable[[], Settings]
    save_settings: Callable[[Settings], Awaitable[None]]
    paused: Callable[[], bool]
    track: Callable[["asyncio.Task[None]"], None]
    on_paired: Callable[[str], Awaitable[None]] = field(default=lambda _: asyncio.sleep(0))


class ChannelService:
    def __init__(self, deps: ChannelDeps, clock: Callable[[], float] = time.time) -> None:
        self._d = deps
        self._clock = clock
        self._channels: dict[str, Channel] = {}
        self._readers: dict[str, asyncio.Task[None]] = {}
        self._codes: dict[str, _Code] = {}
        self._turns: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._approvals: dict[str, _Pending] = {}
        self._told_private: dict[tuple[str, str], float] = {}
        self._limiter = KeyedLimiter(deps.settings().plugins.messages_per_minute)

    # ---- channels ------------------------------------------------------------

    def attach(self, channel: Channel) -> None:
        self._channels[channel.name] = channel
        self._readers[channel.name] = asyncio.create_task(self._read(channel))

    async def detach(self, name: str) -> None:
        self._channels.pop(name, None)
        reader = self._readers.pop(name, None)
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        for key, turn in list(self._turns.items()):
            if key[0] == name:
                turn.cancel()

    async def _read(self, channel: Channel) -> None:
        async for item in channel.receive():
            try:
                if isinstance(item, ApprovalDecision):
                    self._decide(channel.name, item)
                else:
                    await self.handle(channel, item)
            except Exception:
                log.exception("%s: failed to handle a message", channel.name)

    # ---- pairing ---------------------------------------------------------------

    def new_code(self, channel: str) -> tuple[str, int]:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._codes[channel] = _Code(code, self._clock() + CODE_TTL_S)
        return code, CODE_TTL_S

    def paired(self, channel: str) -> list[PairedAccount]:
        return list(self._d.settings().plugins.paired.get(channel, []))

    def is_paired(self, channel: str, user_id: str) -> bool:
        return any(a.user_id == user_id for a in self.paired(channel))

    async def _try_pair(self, channel: Channel, msg: InboundMessage, attempt: str) -> None:
        pending = self._codes.get(channel.name)
        now = self._clock()
        if pending is None or now > pending.expires_at:
            await self._say(
                channel,
                msg,
                "There's no pairing code right now. Create one in the app: Settings → Chat apps.",
            )
            return
        if not secrets.compare_digest(attempt, pending.code):
            pending.tries += 1
            if pending.tries >= CODE_TRIES:
                self._codes.pop(channel.name, None)
            await self._d.store.audit(
                "system", "channel.pair_failed", {"channel": channel.name, "user": msg.user_id}
            )
            await self._say(channel, msg, "That code doesn't match.")
            return
        self._codes.pop(channel.name, None)
        settings = self._d.settings()
        accounts = [a for a in self.paired(channel.name) if a.user_id != msg.user_id]
        accounts.append(PairedAccount(user_id=msg.user_id, name=msg.user_name[:80], paired_at=now))
        plugins = settings.plugins.model_copy(
            update={"paired": {**settings.plugins.paired, channel.name: accounts}}
        )
        await self._d.save_settings(settings.model_copy(update={"plugins": plugins}))
        await self._d.store.audit("user", "channel.paired", {"channel": channel.name, "user": msg.user_id})
        await self._say(channel, msg, f"Paired. You can talk to me here now.\n\n{HELP}")
        await self._d.on_paired(channel.name)

    async def unpair(self, channel: str, user_id: str) -> bool:
        settings = self._d.settings()
        accounts = self.paired(channel)
        kept = [a for a in accounts if a.user_id != user_id]
        if len(kept) == len(accounts):
            return False
        plugins = settings.plugins.model_copy(update={"paired": {**settings.plugins.paired, channel: kept}})
        await self._d.save_settings(settings.model_copy(update={"plugins": plugins}))
        for key, turn in list(self._turns.items()):
            if key[0] == channel:
                turn.cancel()
        await self._d.store.audit("user", "channel.unpaired", {"channel": channel, "user": user_id})
        return True

    # ---- messages --------------------------------------------------------------

    async def handle(self, channel: Channel, msg: InboundMessage) -> None:
        text = msg.text.strip()
        if not self._limiter.allow(f"{channel.name}:{msg.user_id}"):
            return  # flooding: drop silently
        command, _, rest = text.partition(" ")
        if command.lower() == "/pair":
            await self._try_pair(channel, msg, rest.strip())
            return
        if not self.is_paired(channel.name, msg.user_id):
            key = (channel.name, msg.user_id)
            if self._clock() - self._told_private.get(key, 0) > PRIVATE_REPLY_EVERY_S:
                self._told_private[key] = self._clock()
                await self._d.store.audit(
                    "system", "channel.unpaired_message", {"channel": channel.name, "user": msg.user_id}
                )
                await self._say(
                    channel, msg, "This assistant is private. Its owner can pair this account in the app."
                )
            return
        key = (channel.name, msg.conversation_id)
        if command.lower() in ("/start", "/help"):
            await self._say(channel, msg, HELP)
        elif command.lower() == "/stop":
            turn = self._turns.get(key)
            if turn is None:
                await self._say(channel, msg, "Nothing is running.")
            else:
                turn.cancel()
        elif command.lower() == "/new":
            await self._set_session(channel.name, msg.conversation_id, None)
            await self._say(channel, msg, "Started a new chat.")
        elif self._d.paused():
            await self._say(
                channel, msg, "Paused on the computer. Nothing will run until it's resumed there."
            )
        elif key in self._turns:
            await self._say(channel, msg, "Still working on your last message. Send /stop to cancel it.")
        elif text:
            turn = asyncio.create_task(self._run(channel, msg))
            self._turns[key] = turn
            turn.add_done_callback(lambda _: self._turns.pop(key, None))
            self._d.track(turn)

    async def _run(self, channel: Channel, msg: InboundMessage) -> None:
        session_id = await self._d.store.ensure_session(
            await self._session(channel.name, msg.conversation_id), msg.text
        )
        await self._set_session(channel.name, msg.conversation_id, session_id)
        perms = await self._d.chat.permissions(session_id)
        perms.remote = channel.name  # the policy's remote rules for every call in this chat
        await self._d.store.audit("user", "channel.message", {"channel": channel.name, "user": msg.user_id,
                                                              "chars": len(msg.text)})  # fmt: skip
        emitter = _Emitter(self, channel, msg)
        await channel.typing(msg.conversation_id)
        send = ChatSend(request_id=uuid.uuid4().hex, session_id=session_id, text=msg.text)
        await self._d.chat.run(send, emitter)

    def _decide(self, channel: str, decision: ApprovalDecision) -> None:
        pending = self._approvals.get(decision.approval_id)
        # Only the person who was asked, in the channel it was asked in, can answer.
        if pending is None or pending.channel != channel or pending.user_id != decision.user_id:
            return
        if not self.is_paired(channel, decision.user_id):
            return
        self._d.approvals.resolve(decision.approval_id, decision.approved, remember=False)

    async def _say(self, channel: Channel, msg: InboundMessage, text: str) -> None:
        await channel.send(
            OutboundMessage(channel=channel.name, conversation_id=msg.conversation_id, text=text)
        )

    # ---- conversation -> chat session ----------------------------------------

    @property
    def _conn(self) -> aiosqlite.Connection:
        return self._d.store.conn

    async def _session(self, channel: str, conversation_id: str) -> str | None:
        async with self._conn.execute(
            "SELECT session_id FROM channel_conversations WHERE channel=? AND conversation_id=?",
            (channel, conversation_id),
        ) as cur:
            row = await cur.fetchone()
        return str(row[0]) if row else None

    async def _set_session(self, channel: str, conversation_id: str, session_id: str | None) -> None:
        if session_id is None:
            await self._conn.execute(
                "DELETE FROM channel_conversations WHERE channel=? AND conversation_id=?",
                (channel, conversation_id),
            )
        else:
            await self._conn.execute(
                "INSERT INTO channel_conversations(channel, conversation_id, session_id) VALUES(?, ?, ?) "
                "ON CONFLICT(channel, conversation_id) DO UPDATE SET session_id=excluded.session_id",
                (channel, conversation_id, session_id),
            )
        await self._conn.commit()


class _Emitter:
    """A turn's events, as messages in the chat app: the reply, approval buttons, errors."""

    def __init__(self, service: ChannelService, channel: Channel, msg: InboundMessage) -> None:
        self._service = service
        self._channel = channel
        self._msg = msg
        self._parts: list[str] = []

    async def __call__(self, message: ServerMessage) -> None:
        conv = self._msg.conversation_id
        if isinstance(message, ChatDelta):
            self._parts.append(message.text)
        elif isinstance(message, ToolStarted):
            with contextlib.suppress(Exception):
                await self._channel.typing(conv)
        elif isinstance(message, ApprovalRequested):
            self._service._approvals[message.approval_id] = _Pending(
                self._channel.name, conv, self._msg.user_id
            )
            await self._channel.request_approval(
                ApprovalRequest(
                    approval_id=message.approval_id,
                    conversation_id=conv,
                    tool=message.tool,
                    risk=message.risk,
                    summary=message.summary,
                    reason=message.reason,
                )
            )
        elif isinstance(message, ApprovalResolved):
            self._service._approvals.pop(message.approval_id, None)
            await self._channel.close_approval(message.approval_id, conv, message.reason)
        elif isinstance(message, ChatDone):
            await self._reply("".join(self._parts).strip() or "Done.")
        elif isinstance(message, ChatError):
            await self._reply(f"Sorry, that didn't work: {message.message}")
        elif isinstance(message, ChatCancelled):
            await self._reply("Stopped.")

    async def _reply(self, text: str) -> None:
        conv = self._msg.conversation_id
        await self._channel.send(OutboundMessage(channel=self._channel.name, conversation_id=conv, text=text))
