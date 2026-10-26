"""Telegram Bot API client: long polling (no webhook, no public server needed).

Plain text only (no Markdown/HTML parse modes, so nothing in a reply can be turned into
formatting or links). Private chats only. The token is part of every request URL, so
request URLs are never logged and errors are reported by type, never by message.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx

from contracts.channel import (
    MAX_CHANNEL_TEXT,
    ApprovalDecision,
    ApprovalRequest,
    ChannelDecision,
    ChannelInbound,
    ChannelStatus,
    FromChannel,
    InboundMessage,
)

log = logging.getLogger(__name__)
DEFAULT_API = "https://api.telegram.org"
POLL_TIMEOUT_S = 25
MAX_MESSAGE = 4_000  # Telegram allows 4096 characters
RISK_LABEL = {"read": "Reads", "write": "Changes something", "destructive": "Deletes or overwrites",
              "external_send": "Sends something out", "purchase": "Buys something"}  # fmt: skip


class Unauthorized(Exception):
    pass


def chunks(text: str, size: int = MAX_MESSAGE) -> list[str]:
    """Split on paragraph or line breaks where possible."""
    parts: list[str] = []
    while len(text) > size:
        cut = max(text.rfind("\n\n", 0, size), text.rfind("\n", 0, size))
        cut = cut if cut > size // 2 else size
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    return [*parts, text] if text else parts or [""]


class TelegramBot:
    def __init__(
        self,
        token: str,
        emit: Callable[[FromChannel], None],
        api_base: str = DEFAULT_API,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = f"{(api_base or DEFAULT_API).rstrip('/')}/bot{token}"
        self._emit = emit
        self._client = httpx.AsyncClient(transport=transport, timeout=POLL_TIMEOUT_S + 10)
        self._offset = 0
        self._approvals: dict[str, tuple[str, int, str]] = {}  # approval_id -> (chat_id, message_id, text)

    async def call(self, method: str, **params: Any) -> Any:
        try:
            response = await self._client.post(f"{self._base}/{method}", json=params)
        except httpx.HTTPError as exc:
            raise ConnectionError(type(exc).__name__) from None
        if response.status_code in (401, 404):  # bad token: Telegram answers 401 (or 404 for a malformed one)
            raise Unauthorized()
        try:
            body = response.json()
        except ValueError:
            raise ConnectionError(f"HTTP {response.status_code}") from None
        if not body.get("ok"):
            raise ConnectionError(str(body.get("description", f"HTTP {response.status_code}"))[:200])
        return body["result"]

    async def run(self) -> None:
        """Poll until cancelled. Network trouble backs off and retries; a bad token stops."""
        backoff = 1.0
        connected = False
        while True:
            try:
                if not connected:
                    me = await self.call("getMe")
                    self._emit(ChannelStatus(state="connected", detail=f"@{me.get('username', '')}"))
                    connected, backoff = True, 1.0
                updates = await self.call(
                    "getUpdates",
                    offset=self._offset,
                    timeout=POLL_TIMEOUT_S,
                    allowed_updates=["message", "callback_query"],
                )
                for update in updates:
                    self._offset = max(self._offset, int(update["update_id"]) + 1)
                    await self.handle(update)
            except Unauthorized:
                self._emit(ChannelStatus(state="error", detail="Telegram rejected the bot token"))
                return
            except ConnectionError as exc:
                connected = False
                self._emit(ChannelStatus(state="error", detail=f"can't reach Telegram ({exc}); retrying"))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def handle(self, update: dict[str, Any]) -> None:
        if message := update.get("message"):
            chat, sender, text = message.get("chat", {}), message.get("from", {}), message.get("text")
            if chat.get("type") != "private" or not text or sender.get("is_bot"):
                return  # groups and channels are ignored: anyone in them could talk to the assistant
            name = sender.get("username") or sender.get("first_name") or ""
            self._emit(ChannelInbound(message=InboundMessage(
                channel="telegram", conversation_id=str(chat["id"]), user_id=str(sender["id"]),
                user_name=str(name), text=text[:MAX_CHANNEL_TEXT])))  # fmt: skip
        elif query := update.get("callback_query"):
            await self.call("answerCallbackQuery", callback_query_id=query["id"])
            verdict, _, approval_id = str(query.get("data", "")).partition(":")
            if verdict in ("ok", "no") and approval_id:
                decision = ApprovalDecision(
                    approval_id=approval_id, user_id=str(query["from"]["id"]), approved=verdict == "ok"
                )
                self._emit(ChannelDecision(decision=decision))

    async def send(self, chat_id: str, text: str) -> None:
        for part in chunks(text):
            await self.call(
                "sendMessage", chat_id=chat_id, text=part, link_preview_options={"is_disabled": True}
            )

    async def ask(self, request: ApprovalRequest) -> None:
        text = (f"Allow {request.tool}? {RISK_LABEL.get(request.risk, request.risk)}.\n"
                f"{request.summary}\n\nAsking because {request.reason}.")  # fmt: skip
        keyboard = {"inline_keyboard": [[
            {"text": "Approve", "callback_data": f"ok:{request.approval_id}"},
            {"text": "Deny", "callback_data": f"no:{request.approval_id}"},
        ]]}  # fmt: skip
        sent = await self.call(
            "sendMessage", chat_id=request.conversation_id, text=text, reply_markup=keyboard
        )
        self._approvals[request.approval_id] = (request.conversation_id, int(sent["message_id"]), text)

    async def close_approval(self, approval_id: str, outcome: str) -> None:
        found = self._approvals.pop(approval_id, None)
        if found is not None:
            chat_id, message_id, text = found
            # Editing without reply_markup removes the buttons.
            await self.call(
                "editMessageText", chat_id=chat_id, message_id=message_id, text=f"{text}\n\n→ {outcome}"
            )

    async def typing(self, chat_id: str) -> None:
        await self.call("sendChatAction", chat_id=chat_id, action="typing")

    async def close(self) -> None:
        await self._client.aclose()
