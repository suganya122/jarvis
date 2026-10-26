"""Notifications: every open UI tab, plus the desktop (so they show with the browser closed)."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from contracts.common import Trust
from contracts.tools import ToolContext
from contracts.ws import Notify, ServerMessage
from core.config import Settings
from core.store import Store
from core.toolbox import ToolRegistry

log = logging.getLogger(__name__)
Kind = Literal["reminder", "watcher", "task", "routine", "voice"]


class Notifier:
    def __init__(
        self,
        broadcast: Callable[[ServerMessage], Awaitable[None]],
        registry: ToolRegistry,
        settings: Callable[[], Settings],
        store: Store,
    ) -> None:
        self._broadcast = broadcast
        self._registry = registry
        self._settings = settings
        self._store = store

    async def notify(
        self, title: str, body: str, kind: Kind, task_id: str | None = None, in_app: bool = True
    ) -> None:
        message = Notify(notify_id=uuid.uuid4().hex[:12], title=title[:120], body=body[:500], kind=kind,
                         task_id=task_id)  # fmt: skip
        if in_app:
            await self._broadcast(message)
        await self._store.audit("system", "notify", {"kind": kind, "title": message.title})
        tool = self._registry.get("desktop_notify")
        if tool is not None and self._settings().background.desktop_notifications:
            ctx = ToolContext(trace_id="notify", session_id="notify", trust=Trust.USER)
            try:
                await tool.run({"title": message.title, "body": message.body}, ctx)
            except Exception:  # a missing notification daemon must never break a reminder
                log.warning("desktop notification failed", exc_info=True)
