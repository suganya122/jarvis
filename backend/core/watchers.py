"""Watchers: 'tell me when …' conditions, polled deterministically with zero LLM calls.

Checks call tools directly (no per-poll audit spam); the user approved the watcher when it
was created, and a capability turned to 'Never' later pauses it.
"""

import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from contracts.automation import Watcher
from contracts.common import Capability, Trust
from contracts.tools import ToolContext
from core.config import Settings
from core.notify import Notifier
from core.toolbox import ToolRegistry

log = logging.getLogger(__name__)
MAX_PAGE_BYTES = 1_000_000
CAPABILITY = {"file": Capability.FS_READ, "url": Capability.NET_FETCH, "window": Capability.SCREEN_READ}


class CheckError(Exception):
    pass


async def observe_url(url: str, transport: httpx.AsyncBaseTransport | None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(transport=transport, follow_redirects=True, timeout=10) as client:
            response = await client.get(url, headers={"User-Agent": "local-assistant-watcher"})
        body = response.content[:MAX_PAGE_BYTES].decode("utf-8", errors="replace")
        return {"status": response.status_code, "body": body, "error": ""}
    except httpx.HTTPError as exc:
        return {"status": 0, "body": "", "error": type(exc).__name__}


def evaluate(watcher: Watcher, now: dict[str, Any], before: dict[str, Any]) -> tuple[bool, str]:
    """Pure: did the condition happen, and how to describe it."""
    kind, cond = watcher.kind, watcher.condition
    if kind == "url":
        up = 0 < now["status"] < 400
        if cond == "up":
            return up, f"{watcher.target} is up (HTTP {now['status']})"
        if cond == "down":
            return not up, f"{watcher.target} is down ({now['error'] or 'HTTP ' + str(now['status'])})"
        if cond == "contains":
            return watcher.text.casefold() in now[
                "body"
            ].casefold(), f"{watcher.target} now shows “{watcher.text}”"
        digest = hashlib.sha256(now["body"].encode()).hexdigest()
        now["digest"] = digest
        return bool(before) and before.get("digest") != digest, f"{watcher.target} changed"
    if kind == "file":
        if cond == "exists":
            return now["exists"], f"{watcher.target} now exists"
        if cond == "gone":
            return not now["exists"], f"{watcher.target} is gone"
        key = (now["exists"], now["mtime"], now["size"])
        now["key"] = list(key)
        return bool(before) and before.get("key") != list(key), f"{watcher.target} changed"
    titles = now["titles"].casefold()
    present = watcher.target.casefold() in titles
    if cond == "gone":
        return not present, f"No window called “{watcher.target}” is open anymore"
    return present, f"A window called “{watcher.target}” is open"


class WatcherService:
    def __init__(
        self,
        registry: ToolRegistry,
        notifier: Notifier,
        settings: Callable[[], Settings],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._registry = registry
        self._notifier = notifier
        self._settings = settings
        self._transport = transport

    async def observe(self, watcher: Watcher) -> dict[str, Any]:
        if watcher.kind == "url":
            return await observe_url(watcher.target, self._transport)
        ctx = ToolContext(trace_id=f"watcher:{watcher.watcher_id}", session_id="watcher", trust=Trust.USER)
        name, args = (
            ("file_info", {"path": watcher.target}) if watcher.kind == "file" else ("list_windows", {})
        )
        tool = self._registry.get(name)
        if tool is None:
            raise CheckError("the tools this watcher needs aren't running")
        result = await tool.run(args, ctx)
        if watcher.kind == "file":
            if not result.ok:
                raise CheckError(result.output)
            return {k: result.evidence.get(k) for k in ("exists", "mtime", "size")}
        return {"titles": result.output if result.ok else ""}

    def allowed(self, watcher: Watcher) -> bool:
        return self._settings().tools.policies.get(CAPABILITY[watcher.kind], "ask") != "deny"

    async def check(self, watcher: Watcher, state: dict[str, Any]) -> tuple[Watcher, dict[str, Any], bool]:
        """One poll. Returns the updated watcher, its new state, and whether it fired."""
        now_ts = time.time()
        if not self.allowed(watcher):
            paused = watcher.model_copy(update={"status": "paused", "last_detail": "turned off in Settings"})
            return paused, state, False
        try:
            observed = await self.observe(watcher)
        except CheckError as exc:
            failed = watcher.model_copy(
                update={"status": "error", "last_detail": str(exc), "last_checked_at": now_ts}
            )
            return failed, state, False
        fired, detail = evaluate(watcher, observed, state)
        observed.pop("body", None)  # never keep page contents
        observed.pop("titles", None)
        status = "triggered" if fired and watcher.once else watcher.status
        updated = watcher.model_copy(
            update={
                "status": status,
                "last_checked_at": now_ts,
                "last_detail": detail if fired else "checked",
            }
        )
        return updated, observed, fired
