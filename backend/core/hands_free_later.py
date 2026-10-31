"""A spoken request that takes a while goes on working while the conversation carries on: after
MOVE_ON_S Jarvis says it's on it and listens again (core.tasks keeps track of it; "how's it going?"
is a new request, answered from there). What the request says after that waits until Jarvis is
free and nobody is talking: its answer, or a question it needs answered (an approval, which the
next yes or no answers; the overlay card answers it too)."""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable

from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatDelta,
    ChatDone,
    ChatError,
    ServerMessage,
    ToolStarted,
)
from core.speech_text import yes_no
from core.spoken_ask import approval_question

log = logging.getLogger(__name__)
MOVE_ON_S = 15.0
"""A spoken request still working after this long carries on without holding up the conversation."""
ON_IT = "I'm on it, and I'll tell you when it's done. You can keep talking to me."
WAIT_FREE_S = 600.0
POLL_S = 0.3


class Later:
    def __init__(
        self,
        free: Callable[[], bool],
        say: Callable[[str], Awaitable[None]],
        wake: Callable[[], None],
        resolve: Callable[[str, bool], bool],
    ) -> None:
        self._free, self._say, self._wake, self._resolve = free, say, wake, resolve
        self._items: deque[tuple[str, str | None]] = deque()
        self._asked: str | None = None
        """The approval whose question was said last: the next yes or no answers it."""
        self.running: set[asyncio.Task[str]] = set()
        self._sayer: asyncio.Task[None] | None = None

    def keep(self, request: "asyncio.Task[str]") -> Callable[[ServerMessage], None]:
        """It moved on: what it says from now on goes to the returned function, not out loud."""
        self.running.add(request)
        request.add_done_callback(self.running.discard)
        answer: list[str] = []

        def heard(msg: ServerMessage) -> None:
            if isinstance(msg, ChatDelta):
                answer.append(msg.text)
            elif isinstance(msg, ToolStarted):
                answer.clear()  # what it said before a step isn't its answer
            elif isinstance(msg, ApprovalRequested):
                self._tell(approval_question(msg), msg.approval_id)
            elif isinstance(msg, ApprovalResolved):
                self._withdraw(msg.approval_id)
            elif isinstance(msg, ChatDone):
                self._tell("".join(answer).strip() or "That's done.")
            elif isinstance(msg, ChatError):
                self._tell(f"Sorry, that didn't work. {msg.message}")

        return heard

    @property
    def waiting(self) -> bool:
        """It asked a question for a request that moved on, and the answer hasn't come yet."""
        return self._asked is not None

    def answer(self, heard: str) -> bool:
        """A yes or no to the question it just asked for a request that moved on."""
        verdict = yes_no(heard) if self._asked else None
        if self._asked is None or verdict is None:
            return False
        approval, self._asked = self._asked, None
        self._resolve(approval, verdict)
        return True

    def _tell(self, text: str, approval: str | None = None) -> None:
        self._items.append((text, approval))
        if self._sayer is None or self._sayer.done():
            self._sayer = asyncio.create_task(self._say_all())

    def _withdraw(self, approval: str) -> None:
        """Answered (on the card, say): don't ask it, and a yes heard now isn't for it."""
        self._items = deque(item for item in self._items if item[1] != approval)
        if self._asked == approval:
            self._asked = None

    async def _say_all(self) -> None:
        while self._items:
            for _ in range(int(WAIT_FREE_S / POLL_S)):
                if self._free():
                    break
                await asyncio.sleep(POLL_S)
            if not self._items:
                return
            text, approval = self._items.popleft()
            try:
                await self._say(text)
            except Exception:
                log.warning("couldn't say a request's news", exc_info=True)
            if approval is not None:
                self._asked = approval
                self._wake()  # listen for the answer without the wake word
