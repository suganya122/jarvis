"""Speculative start for voice: begin the model on a paused partial transcript.

The model's events are only buffered. Nothing is shown and no tool runs until the real
turn adopts the speculation, which happens only when the final transcript matches the
partial it was started from (same chat, same history, same tier). Otherwise it is
cancelled and its cost, if it already finished, is still recorded.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from contracts.model import Completed, ModelEvent, ModelProvider, ModelRequest, ModelTier

MAX_AGE_S = 60.0


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.casefold()).split())


@dataclass
class Speculation:
    text: str
    session_id: str | None
    history_len: int
    tier: ModelTier
    started: float
    queue: asyncio.Queue[ModelEvent | None] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    completed: Completed | None = None
    used: bool = False

    def matches(
        self, text: str, session_id: str | None, history_len: int, tier: ModelTier, now: float
    ) -> bool:
        return (
            not self.used
            and self.text == normalize(text)
            and (
                self.session_id == session_id
                # started before the chat was saved: the same empty conversation either way
                or (self.session_id is None and self.history_len == 0)
            )
            and self.history_len == history_len
            and self.tier == tier
            and now - self.started < MAX_AGE_S
        )

    async def events(self) -> AsyncIterator[ModelEvent]:
        self.used = True
        while (event := await self.queue.get()) is not None:
            yield event

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()


def start(
    provider: ModelProvider,
    request: ModelRequest,
    *,
    text: str,
    session_id: str | None,
    history_len: int,
    now: float,
) -> Speculation:
    spec = Speculation(
        text=normalize(text), session_id=session_id, history_len=history_len, tier=request.tier, started=now
    )

    async def pump() -> None:
        try:
            async for event in provider.stream(request):
                if isinstance(event, Completed):
                    spec.completed = event
                await spec.queue.put(event)
        finally:
            spec.queue.put_nowait(None)

    spec.task = asyncio.create_task(pump())
    return spec
