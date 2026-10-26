"""The hands-free loop with scripted ears, recogniser and chat: wake word, conversation,
follow-ups, spoken approvals (and the ones that need a click), pause, and the mic being
ignored while it speaks."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

import core.hands_free as hands_free
import core.hands_free_turns as hands_free_turns
from contracts.voice import TranscriptEvent
from contracts.ws import ApprovalRequested, ApprovalResolved, ChatDelta, ChatDone, ServerMessage, Usage
from core.config import VoiceSettings
from core.hands_free import HandsFree
from core.hands_free_types import HandsFreeDeps

LOUD, QUIET = 0.8, 0.0


class FakeEars:
    available: bool | None = True
    detail = "fake mic"

    def __init__(self) -> None:
        self.said: list[str] = []
        self.mic: list[bool] = []
        self.speak_s = 0.05
        self.stopped = 0

    async def listen(self, on: bool) -> None:
        self.mic.append(on)

    async def say(self, text: str, timeout_s: float = 0) -> bool:
        self.said.append(text)
        await asyncio.sleep(self.speak_s)
        return True

    async def stop_saying(self) -> None:
        self.stopped += 1


class FakeRecognizer:
    """Speech-to-text you steer: set `text` and the next fed frame produces a partial."""

    def __init__(self) -> None:
        self.hf: HandsFree | None = None
        self.text = ""
        self.sent = ""
        self.opened: list[str] = []
        self.closed: list[tuple[str, bool]] = []
        self.fed = 0

    async def open(self, stream_id: str) -> None:
        self.opened.append(stream_id)
        self.sent = ""

    async def feed(self, stream_id: str, pcm_b64: str) -> None:
        self.fed += 1
        if self.text and self.text != self.sent and self.hf is not None:
            self.sent = self.text
            self.hf.on_transcript(TranscriptEvent(stream_id=stream_id, kind="partial", text=self.text))

    async def close(self, stream_id: str, abort: bool = False) -> None:
        self.closed.append((stream_id, abort))
        if not abort and self.hf is not None:
            self.hf.on_transcript(TranscriptEvent(stream_id=stream_id, kind="final", text=self.text))
        self.text = ""  # a real recogniser starts fresh for the next stream


class FakeScreen:
    def __init__(self) -> None:
        self.shown: list[tuple[str, str]] = []
        self.hidden = 0
        self.observed: list[ServerMessage] = []

    async def voice_show(
        self, phase: str, partial: str = "", status: str = "", hide_after: float | None = None
    ) -> None:
        self.shown.append((phase, partial))

    async def voice_hide(self) -> None:
        self.hidden += 1

    def voice_begin(self) -> None:
        self.begun = getattr(self, "begun", 0) + 1

    async def voice_rest(self) -> None:
        self.hidden += 1

    async def level(self, value: float) -> None:
        return None

    async def observe(self, msg: ServerMessage) -> None:
        self.observed.append(msg)


class Rig:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hands_free, "TICK_S", 0.02)
        monkeypatch.setattr(hands_free, "SPEAK_TAIL_S", 0.0)
        monkeypatch.setattr(hands_free, "STALLED_EXTRA_S", 5.0)
        self.ears, self.rec, self.screen = FakeEars(), FakeRecognizer(), FakeScreen()
        self.level = QUIET
        self.paused = False
        self.model_ready = True
        self.turns: list[tuple[str, str | None]] = []
        self.resolved: list[tuple[str, bool]] = []
        self.reply = "It is noon."
        self.turn_script: Callable[[Callable[[ServerMessage], Awaitable[None]]], Awaitable[None]] | None = (
            None
        )
        voice = VoiceSettings.model_construct(silence_s=0.15, follow_up_s=0.5)
        self.hf = HandsFree(
            HandsFreeDeps(
                ears=self.ears,
                recognizer=self.rec,
                screen=self.screen,
                settings=lambda: voice,
                paused=lambda: self.paused,
                stt_ready=lambda: self.model_ready,
                run_turn=self._turn,
                resolve_approval=lambda i, ok: self.resolved.append((i, ok)) or True,
            )
        )
        self.rec.hf = self.hf
        self._pump: asyncio.Task[None] | None = None

    async def _turn(
        self, text: str, session: str | None, emit: Callable[[ServerMessage], Awaitable[None]]
    ) -> str:
        self.turns.append((text, session))
        if self.turn_script is not None:
            await self.turn_script(emit)
        else:
            await emit(ChatDelta(request_id="r", text=self.reply))
        await emit(ChatDone(request_id="r", model="m", stop_reason="end_turn", usage=Usage(), cost_usd=0,
                            latency_ms=1, first_token_ms=1, route="model", timings={}))  # fmt: skip
        return "session-1"

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator["Rig"]:
        async def pump() -> None:
            while True:
                self.hf.on_frame("AAAA", self.level)
                await asyncio.sleep(0.01)

        self._pump = asyncio.create_task(pump())
        await self.hf.start()
        try:
            yield self
        finally:
            await self.hf.stop()
            self._pump.cancel()

    async def speak(self, text: str, seconds: float = 0.1) -> None:
        """The user says `text`, then goes quiet."""
        self.level, self.rec.text = LOUD, text
        await asyncio.sleep(seconds)
        self.level = QUIET

    async def until(self, condition: Callable[[], bool], timeout: float = 4.0) -> None:  # noqa: ASYNC109
        async with asyncio.timeout(timeout):
            while not condition():  # noqa: ASYNC110
                await asyncio.sleep(0.01)


def test_wake_word_runs_the_request_and_speaks_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        async with rig.running():
            await rig.until(lambda: rig.ears.mic == [True])
            await rig.speak("jarvis what time is it")
            await rig.until(lambda: rig.ears.said == ["It is noon."])
            assert rig.turns == [("what time is it", None)]
            await rig.until(lambda: rig.hf.state == "idle" and rig.screen.hidden >= 1)  # follow-up timed out
            assert any(p == "listening" for p, _ in rig.screen.shown)

    asyncio.run(run())


def test_talk_that_is_not_for_it_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        async with rig.running():
            await rig.speak("and then he said that we should leave early")
            await rig.until(lambda: any(abort for _, abort in rig.rec.closed))
            await asyncio.sleep(0.3)
            assert rig.turns == [] and rig.ears.said == []

    asyncio.run(run())


def test_the_name_alone_gets_an_acknowledgement_then_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        async with rig.running():
            await rig.speak("jarvis")
            await rig.until(lambda: rig.ears.said == ["Yes?"])
            await rig.until(lambda: rig.hf.state == "listening")
            rig.rec.text = ""
            await rig.speak("open the calendar")
            await rig.until(lambda: len(rig.ears.said) == 2)
            assert rig.turns == [("open the calendar", None)]

    asyncio.run(run())


def test_follow_ups_need_no_wake_word_and_keep_the_chat_going(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        async with rig.running():
            await rig.speak("jarvis what is two plus two")
            await rig.until(lambda: len(rig.ears.said) == 1)
            rig.rec.text = ""
            await rig.until(lambda: rig.hf.state == "listening")
            await rig.speak("and times three")
            await rig.until(lambda: len(rig.turns) == 2)
            assert rig.turns == [("what is two plus two", None), ("and times three", "session-1")]

    asyncio.run(run())


def test_thanks_ends_the_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        async with rig.running():
            await rig.speak("jarvis hello")
            await rig.until(lambda: len(rig.ears.said) == 1)
            rig.rec.text = ""
            await rig.until(lambda: rig.hf.state == "listening")
            await rig.speak("that's all")
            await rig.until(lambda: rig.ears.said[-1] == "Okay.")
            assert len(rig.turns) == 1

    asyncio.run(run())


def _approval(risk: str, untrusted: bool = False) -> ApprovalRequested:
    return ApprovalRequested(approval_id="a1", request_id="r", call_id="c", tool="write_text_file", risk=risk,  # type: ignore[arg-type]
                             summary="Create notes.txt", reason="asks", args={}, untrusted_context=untrusted,
                             can_remember=False, expires_in_s=60)  # fmt: skip


def test_an_ordinary_change_is_approved_by_saying_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)

        async def script(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await emit(_approval("write"))
            await rig.until(lambda: bool(rig.resolved))
            await emit(ApprovalResolved(approval_id="a1", approved=True, reason="ok"))
            await emit(ChatDelta(request_id="r", text="Created."))

        rig.turn_script = script
        async with rig.running():
            await rig.speak("jarvis make a notes file")
            await rig.until(lambda: any("Say yes or no" in t for t in rig.ears.said))
            rig.rec.text = ""
            await asyncio.sleep(0.1)
            await rig.speak("yes please")
            await rig.until(lambda: rig.resolved == [("a1", True)])
            await rig.until(lambda: "Created." in rig.ears.said)

    asyncio.run(run())


def test_a_muddled_or_missing_answer_never_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        monkeypatch.setattr(hands_free_turns, "APPROVAL_LISTEN_S", 0.3)

        async def script(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await emit(_approval("destructive"))
            await rig.until(lambda: bool(rig.resolved), timeout=6)

        rig.turn_script = script
        async with rig.running():
            await rig.speak("jarvis delete the old notes")
            await rig.until(lambda: any("Say yes or no" in t for t in rig.ears.said))
            rig.rec.text = ""
            await asyncio.sleep(0.1)
            await rig.speak("yeah no")
            await rig.until(lambda: rig.resolved == [("a1", False)])

    asyncio.run(run())


@pytest.mark.parametrize(
    ("risk", "untrusted"), [("external_send", False), ("purchase", False), ("write", True)]
)
def test_sends_purchases_and_tainted_requests_need_a_click(
    monkeypatch: pytest.MonkeyPatch, risk: str, untrusted: bool
) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)

        async def script(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await emit(_approval(risk, untrusted))
            await asyncio.sleep(0.5)

        rig.turn_script = script
        async with rig.running():
            await rig.speak("jarvis email the plan")
            await rig.until(lambda: any("on screen" in t for t in rig.ears.said))
            rig.rec.text = ""
            await rig.speak("yes")  # a TV or a stray word can't approve it
            await asyncio.sleep(0.4)
            assert rig.resolved == []
            assert not any("Say yes or no" in t for t in rig.ears.said)

    asyncio.run(run())


def test_it_listens_only_when_it_should(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        monkeypatch.setattr(hands_free, "POLL_S", 0.05)
        rig.model_ready = False
        async with rig.running():
            await rig.until(lambda: "speech model" in rig.hf.detail)
            assert rig.hf.state == "off" and rig.ears.mic == []  # the mic stays closed
            rig.model_ready = True
            await rig.until(lambda: rig.ears.mic == [True])
            rig.paused = True
            await rig.until(lambda: rig.ears.mic == [True, False])  # paused: the OS shows no mic in use
            await rig.until(lambda: rig.hf.detail == "paused")
            assert rig.hf.status() == {"state": "off", "detail": "paused"}
            rig.paused = False
            await rig.until(lambda: rig.hf.state == "idle")

    asyncio.run(run())


def test_the_microphone_is_ignored_while_it_speaks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        rig.ears.speak_s = 0.6
        rig.reply = "jarvis stop"  # its own voice must never wake or command it
        async with rig.running():
            await rig.speak("jarvis hello")
            await rig.until(lambda: rig.hf._speaking)
            fed = rig.rec.fed
            rig.rec.text = "jarvis do something else"
            await asyncio.sleep(0.4)
            assert rig.rec.fed == fed and len(rig.turns) == 1

    asyncio.run(run())


def test_stop_cancels_the_request_and_silences_the_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)

        async def slow(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await asyncio.sleep(30)

        rig.turn_script = slow
        async with rig.running():
            await rig.speak("jarvis write a long report")
            await rig.until(lambda: rig.hf.state == "thinking")
            rig.hf.interrupt()
            await rig.until(lambda: rig.hf.state in ("listening", "idle"))
            assert rig.ears.stopped >= 1 and rig.ears.said == []

    asyncio.run(run())


def test_a_failed_request_is_spoken_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)

        async def boom(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            from contracts.ws import ChatError

            await emit(ChatError(request_id="r", code="provider_error", message="No credit left."))

        rig.turn_script = boom
        async with rig.running():
            await rig.speak("jarvis what is the weather")
            await rig.until(lambda: bool(rig.ears.said))
            assert rig.ears.said[0] == "Sorry, that didn't work. No credit left."

    asyncio.run(run())


def test_the_voice_shortcut_starts_a_conversation_without_the_wake_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        async with rig.running():
            await rig.until(lambda: rig.hf.state == "idle")
            rig.hf.wake_now()
            await rig.until(lambda: rig.ears.said == ["Yes?"])
            await rig.until(lambda: rig.hf.state == "listening")
            await rig.speak("what time is it")
            await rig.until(lambda: rig.turns == [("what time is it", None)])

    asyncio.run(run())


def test_read_aloud_speaks_through_the_same_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        rig.hf.state = "idle"
        assert await rig.hf.say_now("**Hello** from [the docs](https://x.y).") is True
        assert rig.ears.said == ["Hello from the docs."]
        assert rig.hf.state == "idle"  # not stuck on "speaking"

    asyncio.run(run())
