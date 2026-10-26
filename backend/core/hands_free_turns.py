"""One spoken request, and approvals answered by voice: part of the hands-free loop
(core/hands_free.py), kept apart to keep files small. See that module for the rules."""

import asyncio
import logging

from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatCancelled,
    ChatDelta,
    ChatDone,
    ChatError,
    ServerMessage,
)
from core.hands_free_types import (
    APPROVAL_LISTEN_S,
    APPROVAL_SILENCE_S,
    SESSION_TTL_S,
    HandsFreeDeps,
    HandsFreeState,
)
from core.speech_text import MAX_SPOKEN_CHARS, SentenceSplitter, yes_no

log = logging.getLogger(__name__)


class TurnsMixin:
    _d: HandsFreeDeps
    state: HandsFreeState
    _session: str | None
    _session_at: float
    _turn_task: "asyncio.Task[str] | None"

    async def _say(self, text: str) -> None:
        raise NotImplementedError

    async def _hear(self, *, wake: bool, first_word_s: float | None, silence_s: float) -> str | None:
        raise NotImplementedError

    # ---- one request --------------------------------------------------------------------

    async def _turn(self, text: str) -> str | None:
        """Run one request, speaking the reply sentence by sentence as it streams in (the first
        sentence is heard while the rest is still being written). Returns only what is left to
        say: a problem, or nothing."""
        self.state = "thinking"
        outcome: dict[str, str] = {}
        approvals: dict[str, asyncio.Task[None]] = {}
        splitter = SentenceSplitter()
        to_say: asyncio.Queue[str | None] = asyncio.Queue()
        spoken = {"chars": 0, "any": 0}

        async def speak_as_it_streams() -> None:
            while (sentence := await to_say.get()) is not None:
                if spoken["chars"] >= MAX_SPOKEN_CHARS:
                    continue  # long replies: the rest is on screen
                spoken["chars"] += len(sentence)
                spoken["any"] = 1
                cut = spoken["chars"] >= MAX_SPOKEN_CHARS
                await self._say(sentence + (" The rest is on screen." if cut else ""))

        speaker = asyncio.create_task(speak_as_it_streams())

        async def emit(msg: ServerMessage) -> None:
            await self._d.screen.observe(msg)
            if isinstance(msg, ChatDelta):
                for sentence in splitter.push(msg.text):
                    to_say.put_nowait(sentence)
            elif isinstance(msg, ChatError):
                outcome["error"] = msg.message
            elif isinstance(msg, ChatCancelled):
                outcome["cancelled"] = "1"
            elif isinstance(msg, ApprovalRequested):
                approvals[msg.approval_id] = asyncio.create_task(self._ask_approval(msg))
            elif isinstance(msg, ApprovalResolved):
                task = approvals.pop(msg.approval_id, None)
                if task is not None and task is not asyncio.current_task():
                    task.cancel()
            elif isinstance(msg, ChatDone):
                outcome["done"] = "1"

        if self._session and self._d.clock() - self._session_at > SESSION_TTL_S:
            self._session = None
        self._turn_task = asyncio.create_task(self._d.run_turn(text, self._session, emit))
        try:
            self._session = await self._turn_task
            self._session_at = self._d.clock()
        except asyncio.CancelledError:
            speaker.cancel()
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # we're being shut down
            return None  # interrupted with Stop: the turn already reported it
        except Exception:
            speaker.cancel()
            log.exception("voice request failed")
            return "Sorry, something went wrong."
        finally:
            self._turn_task = None
            for task in approvals.values():
                task.cancel()
        if "cancelled" in outcome:
            speaker.cancel()
            return None
        if rest := splitter.flush():
            to_say.put_nowait(rest)
        to_say.put_nowait(None)
        await speaker
        if "error" in outcome:
            return f"Sorry, that didn't work. {outcome['error']}"
        return None if spoken["any"] else "Done."

    async def _ask_approval(self, msg: ApprovalRequested) -> None:
        self.state = "approval"
        summary = msg.summary.split("\n", 1)[0][:160]
        if msg.risk in ("external_send", "purchase") or msg.untrusted_context:
            await self._say(f"I need your OK on screen. {summary}")
            return  # the overlay card's buttons answer it
        await self._say(f"{summary}. Say yes or no.")
        for attempt in range(2):
            heard = await self._hear(wake=False, first_word_s=APPROVAL_LISTEN_S, silence_s=APPROVAL_SILENCE_S)
            verdict = yes_no(heard or "")
            if verdict is not None:
                self._d.resolve_approval(msg.approval_id, verdict)
                return
            if attempt == 0 and heard:
                await self._say("Sorry, yes or no?")
        self._d.resolve_approval(msg.approval_id, False)  # no clear answer: not approved
        await self._say("I'll leave that for now.")
