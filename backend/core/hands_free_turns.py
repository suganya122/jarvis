"""One spoken request, and approvals answered by voice: part of the hands-free loop
(core/hands_free.py), kept apart to keep files small. See that module for the rules."""

import asyncio
import logging
from typing import Any

from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatCancelled,
    ChatDelta,
    ChatDone,
    ChatError,
    ServerMessage,
)
from core.hands_free_barge import BargeIn
from core.hands_free_types import (
    APPROVAL_LISTEN_S,
    APPROVAL_SILENCE_S,
    SESSION_TTL_S,
    HandsFreeDeps,
    HandsFreeState,
)
from core.speech_text import (
    MAX_SPOKEN_CHARS,
    SentenceSplitter,
    is_end_phrase,
    is_filler,
    is_go_offline,
    yes_no,
)

log = logging.getLogger(__name__)


class TurnsMixin:
    _d: HandsFreeDeps
    state: HandsFreeState
    _session: str | None
    _session_at: float
    _turn_task: "asyncio.Task[str] | None"
    _spec: "asyncio.Task[object | None] | None"
    _spec_text: str
    _tasks: "set[asyncio.Future[None]]"
    _inbox: "asyncio.Queue[tuple[str, Any]]"
    _mic_on: bool
    _offline: bool
    _woken: bool
    _speaking: bool
    _barge: BargeIn | None
    _saying: str
    _lead_in: list[str]

    async def _say(self, text: str) -> None:
        raise NotImplementedError

    def _wake_word(self) -> str:
        raise NotImplementedError

    async def _hear(self, *, wake: bool, first_word_s: float | None, silence_s: float) -> str | None:
        raise NotImplementedError

    # ---- controls (menu bar, talk key, Stop, Read aloud) ------------------------------

    async def reopen_mic(self) -> None:
        """Close and reopen the microphone (a different one was chosen)."""
        if self._mic_on:
            await self._d.ears.listen(False)
            await self._d.ears.listen(True)

    def go_offline(self) -> None:
        """End a live session (the menu's "Go offline"): back to waiting for "Hey Jarvis"."""
        self._offline = True
        self._inbox.put_nowait(("offline", None))

    def finish_now(self) -> None:
        """The talk key was let go: what was said so far is the request, no silence wait."""
        self._inbox.put_nowait(("finish", None))

    def wake_now(self) -> None:
        """The voice shortcut: start a conversation as if the wake word had been said."""
        self._woken = True
        self._inbox.put_nowait(("wake", None))

    async def say_now(self, text: str) -> bool:
        """Speak something outside a conversation (Read aloud). Ignored while it's mid-sentence."""
        if self._speaking:
            return False
        await self._say(text)
        return True

    def interrupt(self) -> None:
        """Stop speaking and cancel the running request (the Stop button, the stop shortcut)."""
        if self._turn_task is not None:
            self._turn_task.cancel()
        task = asyncio.ensure_future(self._d.ears.stop_saying())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- starting early, during a pause ------------------------------------------------

    def _maybe_speculate(self, command: str) -> None:
        """A pause mid-request: start the model on the words so far. Nothing it asks to do runs
        until the final words confirm them; if the words change, it's thrown away."""
        if self._d.speculate is None or not command or command == self._spec_text:
            return
        if is_filler(command) or is_end_phrase(command) or is_go_offline(command):
            return
        previous, self._spec_text = self._spec, command
        self._spec = asyncio.create_task(self._d.speculate(command, self._live_session()))
        if previous is not None:
            self._spawn_discard(previous)

    def _spawn_discard(self, task: "asyncio.Task[object | None]") -> None:
        async def drop() -> None:
            spec = await task
            if spec is not None and self._d.discard is not None:
                await self._d.discard(spec)

        future = asyncio.ensure_future(drop())
        self._tasks.add(future)
        future.add_done_callback(self._tasks.discard)

    async def _take_speculation(self) -> object | None:
        task, self._spec, self._spec_text = self._spec, None, ""
        return await task if task is not None else None

    async def _drop_speculation(self) -> None:
        task, self._spec, self._spec_text = self._spec, None, ""
        if task is not None:
            self._spawn_discard(task)

    def _live_session(self) -> str | None:
        if self._session and self._d.clock() - self._session_at > SESSION_TTL_S:
            return None
        return self._session

    async def _turn_listening(self, text: str) -> str | None:
        """One request, with an ear open the whole time: talking over it stops it, and what was
        said becomes the start of the next request."""
        barge = BargeIn(
            self._d.recognizer,
            saying=lambda: self._saying,
            wake_word=self._wake_word(),
            paused=lambda: self.state == "approval",
            on_cut_in=self.interrupt,
        )
        self._barge, self._saying = barge, ""
        watch = asyncio.create_task(barge.run())
        try:
            reply = await self._turn(text, await self._take_speculation())
        finally:
            self._barge = None
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        if barge.cut_in:
            self._lead_in = list(barge.frames)
            return None
        return reply

    def _cut_in(self) -> bool:
        return self._barge is not None and self._barge.cut_in

    # ---- one request --------------------------------------------------------------------

    async def _turn(self, text: str, speculation: object | None = None) -> str | None:
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
                if self._cut_in():
                    continue  # talked over: say nothing more of this reply
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
        self._turn_task = asyncio.create_task(self._d.run_turn(text, self._session, emit, speculation))
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
