"""Hands-free voice: always waiting for the wake word, then a spoken conversation.

    idle ──wake word──▶ listening ──2 s of quiet──▶ thinking ──▶ speaking ──▶ follow-up
      ▲                                                                            │
      └─────────── "that's all" / nothing said for a few seconds ◀────────────────┘

The microphone belongs to the ears process (workers/ears); speech-to-text to the voice worker.
Everything the user says while idle is transcribed on this computer and thrown away unless it
starts with the wake word. Replies are spoken; the microphone is ignored while it speaks (no
echo cancellation outside a browser), so headphones or a pause are needed to interrupt: the
overlay's Stop button and `core.cli stop` work any time.

Approvals: ordinary changes can be answered "yes" / "no". Sends, purchases and anything
influenced by outside content (web pages, files) need a click on the overlay card, so a
misheard word or a TV can never send or pay.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import deque
from typing import Any

from contracts.voice import TranscriptEvent
from core.hands_free_turns import TurnsMixin
from core.hands_free_types import (
    ACK,
    END_ACK,
    FINAL_WAIT_S,
    GATE_HANG_S,
    OFFLINE_ACK,
    POLL_S,
    PREROLL_FRAMES,
    QUIET_FOR_S,
    ROTATE_S,
    SPEAK_TAIL_S,
    STALLED_EXTRA_S,
    TICK_S,
    HandsFreeDeps,
    HandsFreeState,
    NoiseFloor,
)
from core.speech_text import could_be_wake, is_end_phrase, is_filler, is_go_offline, match_wake, speakable

log = logging.getLogger(__name__)


class HandsFree(TurnsMixin):
    def __init__(self, deps: HandsFreeDeps) -> None:
        self._d = deps
        self.state: HandsFreeState = "off"
        self.detail = "starting"
        self._inbox: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._consuming = False
        self._speaking = False
        self._deaf_until = 0.0
        self._stream: str | None = None
        self._main_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[str] | None = None
        self._session: str | None = None
        self._session_at = 0.0
        self._mic_on = False
        self._dropped = False
        self._woken = False
        self.floor = NoiseFloor()
        self._speak_lock = asyncio.Lock()
        self._offline = False
        self._tasks: set[asyncio.Future[None]] = set()

    # ---- lifecycle and inputs -----------------------------------------------------

    async def start(self) -> None:
        if self._main_task is None:
            self._main_task = asyncio.create_task(self._main())

    async def stop(self) -> None:
        task, self._main_task = self._main_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._mic_on:
            with contextlib.suppress(Exception):
                await self._d.ears.listen(False)
            self._mic_on = False

    def on_frame(self, pcm_b64: str, level: float) -> None:
        if self._consuming and not self._speaking and self._d.clock() >= self._deaf_until:
            self._inbox.put_nowait(("frame", (pcm_b64, level)))

    def on_transcript(self, event: TranscriptEvent) -> None:
        if event.stream_id == self._stream:
            self._inbox.put_nowait(("stt", event))

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

    def _set(self, state: HandsFreeState, detail: str) -> None:
        if (state, detail) != (self.state, self.detail) and state in ("off", "idle") and state != self.state:
            log.info("hands-free %s: %s", state, detail)  # what, never what was heard
        self.state, self.detail = state, detail

    def status(self) -> dict[str, str]:
        return {"state": self.state, "detail": self.detail}

    # ---- the loop -----------------------------------------------------------------------

    def _wake_word(self) -> str:
        return self._d.settings().wake_word

    def _blocker(self) -> str | None:
        """Why it isn't listening right now, or None if it should be."""
        voice = self._d.settings()
        if not voice.hands_free:
            return "turned off in Settings → Voice"
        if self._d.paused():
            return "paused"
        if not self._d.stt_ready():
            return "needs a speech model: download one in Settings → Voice"
        if self._d.ears.available is not True:
            return self._d.ears.detail or "no microphone"
        return None

    async def _main(self) -> None:
        while True:
            try:
                problem = self._blocker()
                if problem is not None:
                    await self._idle_off(problem)
                    await asyncio.sleep(POLL_S)
                    continue
                if not self._mic_on:
                    await self._d.ears.listen(True)
                    self._mic_on = True
                self._set("idle", f"waiting for “Hey {self._wake_word().title()}”")
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("hands-free loop error; carrying on")
                await asyncio.sleep(POLL_S)

    async def _idle_off(self, problem: str) -> None:
        self._set("off", problem)
        if self._mic_on:  # paused or switched off: the OS shows no microphone in use
            with contextlib.suppress(Exception):
                await self._d.ears.listen(False)
            self._mic_on = False

    async def _cycle(self) -> None:
        heard = await self._hear(wake=True, first_word_s=None, silence_s=self._d.settings().silence_s)
        if self._woken:  # the shortcut: no wake word needed
            self._woken = False
            self._d.on_wake()
            self._d.screen.voice_begin()
            await self._d.screen.voice_show("listening", status="Listening…")
            await self._conversation("", ack=False)
            await self._d.screen.voice_rest()
            return
        if self._dropped:  # someone else's conversation: wait for it to stop, don't reopen mid-sentence
            self._dropped = False
            await self._wait_quiet()
        if heard is None:
            return
        addressed, command = match_wake(heard, self._wake_word())
        if not addressed:
            return  # talk that wasn't for us: gone
        self._d.on_wake()
        self._d.screen.voice_begin()
        await self._conversation(command)
        await self._d.screen.voice_rest()  # the conversation stays in the box for a while

    async def _conversation(self, text: str, ack: bool = True) -> None:
        """ack: say "Yes?" when called by name alone. The talk key skips it: the box already
        shows it's listening, and every spoken word costs time."""
        silence_s = self._d.settings().silence_s
        live = self._d.settings().stay_live
        self._offline = False
        pending: str | None = text
        while True:
            if pending is None:  # live and nothing (usable) heard: keep listening
                pass
            elif not pending:
                if ack:
                    await self._say(ACK)
                ack = True
            elif live and is_go_offline(pending):
                await self._say(OFFLINE_ACK)
                return
            elif live and is_end_phrase(pending):
                await self._say("You're welcome.")  # politeness doesn't end a live session
            elif not live and is_end_phrase(pending):
                await self._say(END_ACK)
                return
            elif not (live and is_filler(pending)):
                reply = await self._turn(pending)
                if reply:
                    await self._say(reply)
            if self._blocker() is not None or self._offline:
                return
            status = "Live: just talk. Say “go offline” to stop." if live else "Go ahead…"
            await self._d.screen.voice_show("listening", status=status)
            self.state = "listening"
            follow_up = None if live else self._d.settings().follow_up_s
            heard = await self._hear(wake=False, first_word_s=follow_up, silence_s=silence_s)
            if heard is None:
                if live and not self._offline and self._blocker() is None:
                    pending = None
                    continue
                return
            addressed, rest = match_wake(heard, self._wake_word())
            pending = rest if addressed else heard

    # ---- hearing ------------------------------------------------------------------------

    async def _hear(self, *, wake: bool, first_word_s: float | None, silence_s: float) -> str | None:
        """One utterance: from the first word until `silence_s` of quiet. With `wake`, anything
        that doesn't start with the wake word is dropped as soon as that's clear. None when
        nothing (usable) was said."""
        stream = f"hf-{uuid.uuid4().hex[:12]}"
        while not self._inbox.empty():
            self._inbox.get_nowait()
        self._stream = stream
        await self._d.recognizer.open(stream)
        self._consuming = True
        now = self._d.clock()
        started = last_sound = last_change = now
        text, addressed, final = "", not wake, None
        preroll: deque[str] = deque(maxlen=PREROLL_FRAMES)
        gate_until = 0.0
        try:
            while True:
                try:
                    kind, data = await asyncio.wait_for(self._inbox.get(), TICK_S)
                except TimeoutError:
                    kind, data = "tick", None
                now = self._d.clock()
                if self._blocker() is not None:
                    return None
                if kind == "wake" and wake:
                    return None  # the shortcut was pressed: _cycle starts a conversation
                if kind == "offline" and not wake:
                    return None
                if kind == "finish":
                    if not text:
                        return None  # let go before saying anything
                    final = await self._finish(stream, text)
                    break
                if kind == "frame":
                    pcm_b64, level = data
                    self.floor.update(level)
                    if (not wake or addressed) and text:
                        await self._d.recognizer.feed(stream, pcm_b64)
                    else:  # nothing heard yet: only send sound, with a little lead-in
                        if level >= self.floor.gate():
                            if now >= gate_until:
                                for old in preroll:
                                    await self._d.recognizer.feed(stream, old)
                                preroll.clear()
                            gate_until = now + GATE_HANG_S
                        if now < gate_until:
                            await self._d.recognizer.feed(stream, pcm_b64)
                        else:
                            preroll.append(pcm_b64)
                    if level >= self.floor.sound():
                        last_sound = now
                    if addressed:
                        await self._d.screen.level(level)
                elif kind == "stt":
                    event: TranscriptEvent = data
                    if event.kind == "final":
                        final = event.text
                        break
                    if event.text and event.text != text:
                        text, last_change = event.text, now
                        last_sound = max(last_sound, now)
                        if wake and not addressed:
                            if match_wake(text, self._wake_word())[0]:
                                addressed = True
                            elif not could_be_wake(text, self._wake_word()):
                                self._dropped = True
                                return None  # someone else's conversation
                        if addressed:
                            await self._show_heard(text)
                if text and (
                    now - last_sound >= silence_s or now - last_change >= silence_s + STALLED_EXTRA_S
                ):
                    final = await self._finish(stream, text)
                    break
                if not text and first_word_s is not None and now - started >= first_word_s:
                    return None
                if not text and now - started >= ROTATE_S:
                    return None  # reopen: the engine limits how long one stream can run
        finally:
            self._consuming = False
            await self._drop_stream(stream, abort=final is None)
        return (final or text).strip() or None

    async def _wait_quiet(self) -> None:
        """Let the room go quiet (frames are read, nothing is transcribed)."""
        self._consuming = True
        try:
            quiet_since, deadline = self._d.clock(), self._d.clock() + 30.0
            while self._d.clock() < deadline and self._blocker() is None:
                try:
                    kind, data = await asyncio.wait_for(self._inbox.get(), TICK_S)
                except TimeoutError:
                    kind, data = "tick", None
                now = self._d.clock()
                if kind == "frame":
                    self.floor.update(data[1])
                if kind == "frame" and data[1] >= self.floor.gate():
                    quiet_since = now
                if now - quiet_since >= QUIET_FOR_S:
                    return
        finally:
            self._consuming = False

    async def _show_heard(self, text: str) -> None:
        self.state = "listening"
        await self._d.screen.voice_show("listening", partial=text, status="Listening…")

    async def _finish(self, stream: str, fallback: str) -> str:
        """Ask the recogniser to flush, and take its final text (or what we had)."""
        await self._d.recognizer.close(stream)
        deadline = self._d.clock() + FINAL_WAIT_S
        while self._d.clock() < deadline:
            try:
                kind, data = await asyncio.wait_for(self._inbox.get(), TICK_S)
            except TimeoutError:
                continue
            if kind == "stt" and data.kind == "final":
                return str(data.text) or fallback
        return fallback

    async def _drop_stream(self, stream: str, abort: bool) -> None:
        self._stream = None
        if abort:
            with contextlib.suppress(Exception):
                await self._d.recognizer.close(stream, abort=True)

    # ---- speaking -----------------------------------------------------------------------

    async def _say(self, text: str) -> None:
        spoken, cut = speakable(text)
        if not spoken:
            return
        if cut:
            spoken += " The rest is on screen."
        async with self._speak_lock:  # one voice at a time: a reply's sentences, then a question
            await self._say_now(spoken)

    async def _say_now(self, spoken: str) -> None:
        before, self.state, self._speaking = self.state, "speaking", True
        try:
            await self._d.ears.say(spoken)
        except TimeoutError:
            log.warning("speaking took too long; moving on")
        finally:
            self._speaking = False
            if self.state == "speaking":
                self.state = before  # e.g. back to waiting after a Read aloud
            self._deaf_until = self._d.clock() + SPEAK_TAIL_S
