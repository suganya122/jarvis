"""The hands-free loop with scripted ears, recogniser and chat: wake word, conversation,
follow-ups, spoken approvals (and the ones that need a click), pause, and the mic being
ignored while it speaks."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

import core.hands_free as hands_free
import core.hands_free_later as hands_free_later
import core.hands_free_turns as hands_free_turns
import core.spoken_ask as spoken_ask
from contracts.voice import TranscriptEvent
from contracts.ws import (
    ApprovalRequested,
    ApprovalResolved,
    ChatDelta,
    ChatDone,
    ServerMessage,
    ToolFinished,
    ToolStarted,
    Usage,
)
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
        self.problem = ""  # set: the engine reports this instead of hearing anything

    async def open(self, stream_id: str) -> None:
        self.opened.append(stream_id)
        self.sent = ""

    async def feed(self, stream_id: str, pcm_b64: str) -> None:
        self.fed += 1
        if self.problem and self.hf is not None:
            event = TranscriptEvent(stream_id=stream_id, kind="final", text="", ms=-1, error=self.problem)
            self.hf.on_transcript(event)
            return
        if self.text and self.text != self.sent and self.hf is not None:
            self.sent = self.text
            self.hf.on_transcript(TranscriptEvent(stream_id=stream_id, kind="partial", text=self.text))

    async def close(self, stream_id: str, abort: bool = False) -> None:
        self.closed.append((stream_id, abort))
        if stream_id.startswith("hf-cut-"):
            return  # the cut-in listener letting go: the person may still be talking
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
    def __init__(self, monkeypatch: pytest.MonkeyPatch, live: bool = False, silence_s: float = 0.15) -> None:
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
        self.speculated: list[str] = []
        self.discarded: list[object] = []
        self.used_speculation: list[object | None] = []
        self.turn_script: Callable[[Callable[[ServerMessage], Awaitable[None]]], Awaitable[None]] | None = (
            None
        )
        voice = VoiceSettings.model_construct(silence_s=silence_s, follow_up_s=0.5, stay_live=live)
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
                speculate=self._speculate,
                discard=self._discard,
            )
        )
        self.rec.hf = self.hf
        self._pump: asyncio.Task[None] | None = None

    async def _turn(
        self,
        text: str,
        session: str | None,
        emit: Callable[[ServerMessage], Awaitable[None]],
        speculation: object | None = None,
    ) -> str:
        self.turns.append((text, session))
        self.used_speculation.append(speculation)
        if self.turn_script is not None:
            await self.turn_script(emit)
        else:
            await emit(ChatDelta(request_id="r", text=self.reply))
        await emit(ChatDone(request_id="r", model="m", stop_reason="end_turn", usage=Usage(), cost_usd=0,
                            latency_ms=1, first_token_ms=1, route="model", timings={}))  # fmt: skip
        return "session-1"

    async def _speculate(self, text: str, session: str | None) -> object | None:
        self.speculated.append(text)
        return f"spec:{text}"

    async def _discard(self, spec: object) -> None:
        self.discarded.append(spec)

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


def _approval(risk: str, summary: str = "Create notes.txt") -> ApprovalRequested:
    return ApprovalRequested(approval_id="a1", request_id="r", call_id="c", tool="write_text_file", risk=risk,  # type: ignore[arg-type]
                             summary=summary, reason="asks", args={}, untrusted_context=True,
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
            await rig.until(lambda: "Create notes.txt. Go ahead?" in rig.ears.said)
            rig.rec.text = ""
            await asyncio.sleep(0.1)
            await rig.speak("yes please")
            await rig.until(lambda: rig.resolved == [("a1", True)])
            await rig.until(lambda: "Created." in rig.ears.said)

    asyncio.run(run())


def test_only_a_clear_answer_counts_and_silence_keeps_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence used to count as a no ("I'll leave that for now"), so a tap on the card a moment
    later found nothing to approve. Now silence waits (the card stays up until the approval times
    out), something unclear gets one "Yes or no?", and a muddled answer is a no, never a yes."""

    async def run() -> None:
        rig = Rig(monkeypatch)
        monkeypatch.setattr(hands_free_turns, "APPROVAL_LISTEN_S", 0.2)

        async def script(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await emit(_approval("destructive"))
            await rig.until(lambda: bool(rig.resolved), timeout=6)

        rig.turn_script = script
        async with rig.running():
            await rig.speak("jarvis delete the old notes")
            await rig.until(lambda: any("Go ahead?" in t for t in rig.ears.said))
            rig.rec.text = ""
            await asyncio.sleep(0.8)  # several listening rounds with nothing said
            assert rig.resolved == [] and rig.hf.state == "approval"
            await rig.speak("hmm let me think")
            await rig.until(lambda: rig.ears.said[-1] == "Yes or no?")
            rig.rec.text = ""
            await asyncio.sleep(0.1)
            await rig.speak("yeah no")
            await rig.until(lambda: rig.resolved == [("a1", False)])

    asyncio.run(run())


@pytest.mark.parametrize(
    ("risk", "said"), [("external_send", "This sends something"), ("purchase", "This spends money")]
)
def test_serious_steps_say_what_kind_they_are_then_a_spoken_yes_approves(
    monkeypatch: pytest.MonkeyPatch, risk: str, said: str
) -> None:
    """The user's choice: a spoken yes or the button approves anything, sends and purchases too.
    So what kind of step it is is said before the yes counts."""

    async def run() -> None:
        rig = Rig(monkeypatch)

        async def script(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await emit(_approval(risk))
            await asyncio.sleep(1.5)

        rig.turn_script = script
        async with rig.running():
            await rig.speak("jarvis email the plan")
            await rig.until(lambda: f"{said}: Create notes.txt. Go ahead?" in rig.ears.said)
            rig.rec.text = ""
            await rig.speak("yes")
            await rig.until(lambda: rig.resolved == [("a1", True)])

    asyncio.run(run())


def test_the_question_is_a_few_words_not_the_whole_card() -> None:
    goal = (
        "Voice check for every request: verify the speaker is the user (srinivasan). If there is "
        "anyone else talking, ignore them and say nothing"
    )
    question = spoken_ask.approval_question(_approval("write", f"Build a new ability: {goal}"))
    assert question == "Build a new ability: Voice check for every request. Go ahead?"
    pay = "Pay ₹134 to Blinkit for 1 Amul Taaza Toned Milk 1 ltr (₹77 + delivery and surge) (push button)"
    question = spoken_ask.approval_question(_approval("purchase", pay))
    assert question == "This spends money: Pay ₹134 to Blinkit for 1 Amul Taaza Toned Milk 1 ltr. Go ahead?"
    bill = "Pay TNPDCL electricity bill Rs. 1,245.00 for consumer 06-345-012-187 (INDU S), via BillDesk"
    question = spoken_ask.approval_question(_approval("purchase", bill))
    said = "Pay TNPDCL electricity bill Rs. 1,245.00 for consumer 06-345-012-187"
    assert question == f"This spends money: {said}. Go ahead?"  # the amount is kept, not cut at "Rs."


@pytest.mark.parametrize(
    ("risk", "summary", "spoken"),
    [
        ("purchase", "Request Uber Go from Home, 12 Usman Road, T. Nagar to Chennai Central Railway Station, "
         "₹245, paid with HDFC Bank Visa •••• 4242 (button 'Request Uber Go')",
         "This spends money: Request Uber Go from Home, 12 Usman Road, ₹245. Go ahead?"),
        ("destructive", "Deletes files: `find ~/Downloads -maxdepth 1 -type f -mtime +30 -delete`",
         "Deletes files in Downloads. Go ahead?"),
        ("destructive", "Stops running programs: `killall Spotify`",
         "Stops running programs: killall Spotify. Go ahead?"),
        ("external_send", "Send from this computer: `curl -F f=@notes.txt https://paste.example.com/up`",
         "This sends something: Send from this computer to paste.example.com. Go ahead?"),
    ],
)  # fmt: skip
def test_a_command_is_not_read_out_and_the_amount_always_is(risk: str, summary: str, spoken: str) -> None:
    """Heard on the copies of Uber and Terminal: the Uber question stopped at "T." of "T. Nagar",
    before the fare, and a delete read out "find tilde slash Downloads dash mindepth..."."""
    assert spoken_ask.approval_question(_approval(risk, summary)) == spoken


def test_saying_yes_over_the_question_approves_instead_of_cancelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """What happened: while it was still asking, "yes, go ahead, build it" was taken for talking
    over it, which cancelled the whole request, so it said "the approval was cancelled"."""

    async def run() -> None:
        rig = Rig(monkeypatch, live=True)

        async def script(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await emit(_approval("write", "Build a new ability: Voice check for every request"))
            await rig.until(lambda: bool(rig.resolved), timeout=6)
            await emit(ApprovalResolved(approval_id="a1", approved=True, reason="approved by you"))
            await emit(ChatDelta(request_id="r", text="Built it."))

        rig.turn_script = script
        async with rig.running():
            rig.ears.speak_s = 0.8
            await rig.speak("jarvis build yourself a voice check")
            await rig.until(lambda: any("Go ahead?" in t for t in rig.ears.said))
            await rig.speak("yes please do that right now")  # over the question, still being said
            await rig.until(lambda: rig.resolved == [("a1", True)])
            await rig.until(lambda: "Built it." in rig.ears.said)
            assert rig.ears.stopped == 0 and len(rig.turns) == 1

    asyncio.run(run())


@pytest.mark.parametrize(("heard", "stops"), [("are you building it yet", False), ("stop", True)])
def test_while_a_step_runs_only_stop_words_cut_it_off(
    monkeypatch: pytest.MonkeyPatch, heard: str, stops: bool
) -> None:
    """A build takes a minute or two: "are you building it?" mustn't throw it away. "Stop" does."""

    async def run() -> None:
        rig = Rig(monkeypatch, live=True)

        async def script(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            started = ToolStarted(request_id="r", call_id="c1", tool="build", summary="B", risk="write")
            await emit(started)
            await asyncio.sleep(1.0)
            await emit(ToolFinished(request_id="r", call_id="c1", status="ok", preview="", duration_ms=1))
            await emit(ChatDelta(request_id="r", text="Built it."))

        rig.turn_script = script
        async with rig.running():
            await rig.speak("jarvis build yourself a voice check")
            await rig.until(lambda: rig.hf.state == "thinking")
            await asyncio.sleep(0.2)
            await rig.speak(heard)
            if stops:
                await rig.until(lambda: rig.ears.stopped >= 1)
                await asyncio.sleep(1.2)
                assert "Built it." not in rig.ears.said
            else:
                await rig.until(lambda: "Built it." in rig.ears.said)
                assert rig.ears.stopped == 0

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
            rig.rec.text = "jarvis stop"  # what it's saying, coming back through the microphone
            await asyncio.sleep(0.4)
            assert rig.ears.stopped == 0 and len(rig.turns) == 1

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
            await rig.until(lambda: rig.hf.state == "listening")
            assert rig.ears.said == []  # the talk key: straight to listening, no "Yes?"
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


def test_the_first_sentence_is_spoken_before_the_reply_has_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        heard_before_end: list[list[str]] = []

        async def streaming(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await emit(ChatDelta(request_id="r", text="The flight to Delhi leaves at six in the morning. "))
            await asyncio.sleep(0.3)  # the model is still writing
            heard_before_end.append(list(rig.ears.said))
            await emit(ChatDelta(request_id="r", text="It costs about four thousand rupees, one way."))

        rig.turn_script = streaming
        async with rig.running():
            await rig.speak("jarvis when is the next flight to delhi")
            await rig.until(lambda: len(rig.ears.said) == 2)
            assert heard_before_end == [["The flight to Delhi leaves at six in the morning."]]
            assert rig.ears.said[1] == "It costs about four thousand rupees, one way."

    asyncio.run(run())


def test_letting_go_of_the_talk_key_sends_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        voice = VoiceSettings.model_construct(silence_s=5.0, follow_up_s=0.5)  # a long silence wait…
        rig.hf._d.settings = lambda: voice
        async with rig.running():
            await rig.until(lambda: rig.hf.state == "idle")
            rig.level, rig.rec.text = LOUD, "jarvis what time is it"
            await rig.until(lambda: rig.hf.state == "listening")
            started = asyncio.get_running_loop().time()
            rig.hf.finish_now()  # …but the key was let go
            await rig.until(lambda: len(rig.turns) == 1, timeout=2)
            assert asyncio.get_running_loop().time() - started < 1.0

    asyncio.run(run())


def test_a_live_session_stays_on_until_go_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch, live=True)
        async with rig.running():
            await rig.speak("hey jarvis what time is it")
            await rig.until(lambda: rig.ears.said == ["It is noon."])
            rig.rec.text = ""
            await asyncio.sleep(1.5)  # far past follow_up_s (0.5 s): still live, no wake word needed
            assert rig.hf.state == "listening"
            await rig.speak("um")  # a stray sound isn't a request
            await asyncio.sleep(0.4)
            assert len(rig.turns) == 1
            rig.rec.text = ""
            await rig.speak("and what's the date")
            await rig.until(lambda: len(rig.turns) == 2)
            rig.rec.text = ""
            await rig.until(lambda: rig.hf.state == "listening")
            await rig.speak("thanks")  # politeness doesn't end it
            await rig.until(lambda: rig.ears.said[-1] == "You're welcome.")
            rig.rec.text = ""
            await rig.until(lambda: rig.hf.state == "listening")
            await rig.speak("go offline")
            await rig.until(lambda: rig.ears.said[-1].startswith("Going offline"))
            await rig.until(lambda: rig.hf.state == "idle")  # back to waiting for "Hey Jarvis"
            assert [t for t, _ in rig.turns] == ["what time is it", "and what's the date"]

    asyncio.run(run())


def test_the_menus_go_offline_ends_a_live_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch, live=True)
        async with rig.running():
            await rig.speak("hey jarvis hello there")
            await rig.until(lambda: len(rig.ears.said) == 1)
            rig.rec.text = ""
            await rig.until(lambda: rig.hf.state == "listening")
            rig.hf.go_offline()
            await rig.until(lambda: rig.hf.state == "idle")

    asyncio.run(run())


def test_a_pause_starts_the_model_early_and_the_request_uses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        monkeypatch.setattr(hands_free, "SPECULATE_PAUSE_S", 0.05)
        async with rig.running():
            await rig.speak("hey jarvis what time is it")
            await rig.until(lambda: len(rig.turns) == 1)
            assert rig.speculated == ["what time is it"]  # the wake word isn't part of the request
            assert rig.used_speculation == ["spec:what time is it"]  # the head start was used
            assert rig.discarded == []

    asyncio.run(run())


def test_a_speech_engine_problem_is_said_once_and_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dictation off on a Mac: say why it can't hear (once), show it, and try again later."""
    monkeypatch.setattr(hands_free, "ENGINE_RETRY_S", 0.5)

    async def run() -> None:
        rig = Rig(monkeypatch)
        rig.rec.problem = "Dictation is off, so I can't hear you."
        async with rig.running():
            await rig.speak("jarvis what time is it")
            await rig.until(lambda: rig.hf.state == "off")
            assert rig.hf.detail == rig.rec.problem
            await rig.until(lambda: rig.ears.said == [rig.rec.problem])
            rig.rec.problem = ""  # turned on: after the retry wait it hears again
            await rig.until(lambda: rig.hf.state == "idle", timeout=6.0)
            await rig.speak("jarvis what time is it")
            await rig.until(lambda: rig.ears.said[-1:] == ["It is noon."])
            assert rig.ears.said.count("Dictation is off, so I can't hear you.") == 1

    asyncio.run(run())


def test_talking_over_the_reply_stops_it_and_starts_the_next_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Like talking over a person: "no wait, the other screen" cuts the reply off at once, and
    those words are the next request (heard from the first word)."""

    async def run() -> None:
        rig = Rig(monkeypatch, live=True)

        async def long_reply(emit: Callable[[ServerMessage], Awaitable[None]]) -> None:
            await emit(ChatDelta(request_id="r", text="You are on the left screen. "))
            await asyncio.sleep(1.0)  # still going when they cut in
            await emit(ChatDelta(request_id="r", text="It has three windows open. "))

        rig.turn_script = long_reply
        async with rig.running():
            await rig.speak("jarvis which screen am i on")
            await rig.until(lambda: rig.ears.said == ["You are on the left screen."])
            await rig.speak("no wait the other screen")
            await rig.until(lambda: rig.ears.stopped >= 1)
            await rig.until(lambda: len(rig.turns) == 2)
            assert rig.turns[1][0] == "no wait the other screen"
            assert "It has three windows open." not in rig.ears.said

    asyncio.run(run())


def test_its_own_voice_coming_back_is_not_a_cut_in(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch, live=True)
        rig.reply = "You are on the left screen with three windows open."
        said = rig.ears.say

        async def echoing(text: str, timeout_s: float = 0) -> bool:
            rig.rec.text = text.lower().rstrip(".")  # the speakers reach the microphone
            done = await said(text, timeout_s)
            rig.rec.text = ""  # and stop when it does
            return done

        rig.ears.say = echoing  # type: ignore[method-assign]
        async with rig.running():
            await rig.speak("jarvis which screen am i on")
            await rig.until(lambda: rig.ears.said == [rig.reply])
            await asyncio.sleep(0.3)
            assert rig.ears.stopped == 0 and len(rig.turns) == 1

    asyncio.run(run())


def test_wait_just_makes_it_listen(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch, live=True)
        async with rig.running():
            await rig.speak("jarvis wait")
            await rig.until(lambda: rig.hf.state == "listening")
            await asyncio.sleep(0.4)
            assert rig.turns == [] and rig.ears.said == []
            await rig.speak("what time is it")
            await rig.until(lambda: rig.ears.said == ["It is noon."])

    asyncio.run(run())


def test_a_thinking_pause_in_a_long_request_does_not_cut_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch, silence_s=0.4)
        async with rig.running():
            await rig.speak("jarvis send a message to srinith and")
            await asyncio.sleep(0.55)  # past the 0.4 s pause, but the sentence was left open (0.8 s)
            assert rig.turns == []
            await rig.speak("jarvis send a message to srinith and tell him i will be late today")
            await rig.until(lambda: rig.ears.said == ["It is noon."])
            assert rig.turns == [("send a message to srinith and tell him i will be late today", None)]

    asyncio.run(run())


def test_the_cut_in_listeners_transcripts_reach_the_voice_loop() -> None:
    """The daemon routes transcripts by stream name: the cut-in listener's must come back to it
    (they once went to the chat window's dictation instead, so "wait" was never heard)."""
    from core.hands_free_barge import STREAM_PREFIX
    from core.voice_loop import VoiceLoop

    assert VoiceLoop.owns(TranscriptEvent(stream_id=f"{STREAM_PREFIX}abc", kind="partial", text="wait"))


def test_words_before_a_pause_are_kept_when_the_recogniser_starts_its_text_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch, silence_s=0.4)
        async with rig.running():
            await rig.speak("jarvis send a message to srinith and")
            await asyncio.sleep(0.1)
            await rig.speak("tell him")  # the engine's text starts over: only what came after the gap
            await rig.speak("tell him i will be late")
            await rig.until(lambda: rig.ears.said == ["It is noon."])
            assert rig.turns[0][0] == "send a message to srinith and tell him i will be late"

    asyncio.run(run())


def test_an_ability_that_listens_can_stop_a_request_being_acted_on(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        heard: list[str] = []

        async def only_me(audio: bytes, text: str) -> tuple[bool, str]:
            heard.append(text)
            return ("news" not in text, "I don't recognise this voice (match 0.31)" if "news" in text else "")

        rig.hf._d.check_voice = only_me
        rig.rec.last_audio = lambda: b"\x00\x00" * 160  # type: ignore[attr-defined]
        async with rig.running():
            await rig.speak("jarvis the news at nine")  # the TV, say: not acted on, and it says so
            await rig.until(lambda: heard == ["the news at nine"])
            await rig.until(lambda: len(rig.ears.said) == 1)
            assert rig.turns == []
            assert rig.ears.said[0].startswith(
                "I didn't act on that: I don't recognise this voice. If that was you"
            )
            await rig.speak("the news at ten")  # again: said shorter
            await rig.until(lambda: len(rig.ears.said) == 2)
            assert rig.ears.said[1] == "I didn't act on that: I don't recognise this voice."
            await rig.speak("jarvis what time is it")
            await rig.until(lambda: rig.ears.said[-1:] == ["It is noon."])
            assert [t for t, _ in rig.turns] == ["what time is it"]

    asyncio.run(run())


def test_live_it_doesnt_answer_every_sentence_it_holds_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch, live=True)

        async def only_me(audio: bytes, text: str) -> tuple[bool, str]:
            return "tv" not in text, "I don't recognise this voice"

        rig.hf._d.check_voice = only_me
        rig.rec.last_audio = lambda: b"\x00\x00" * 160  # type: ignore[attr-defined]
        async with rig.running():
            await rig.speak("jarvis tv news")
            await rig.until(lambda: len(rig.ears.said) == 1)
            for line in ("tv weather", "tv sport"):
                await rig.speak(line)
                await asyncio.sleep(0.3)
            assert len(rig.ears.said) == 1 and rig.turns == []

    asyncio.run(run())


def test_a_voice_check_that_isnt_working_is_said_once_and_it_answers_anyway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        rig = Rig(monkeypatch)
        broken = [True]

        async def flaky(audio: bytes, text: str) -> tuple[bool, str]:
            if text == "crash":
                raise RuntimeError("boom")
            return True, "my voice guard ability isn't working (it crashed: no module numpy)" if broken[
                0
            ] else ""

        rig.hf._d.check_voice = flaky
        rig.rec.last_audio = lambda: b"\x00\x00" * 160  # type: ignore[attr-defined]
        async with rig.running():
            await rig.speak("jarvis what time is it")
            await rig.until(lambda: len(rig.ears.said) == 2)
            heads_up = "Heads up: my voice guard ability isn't working, so I'm answering anyone for now."
            assert rig.ears.said == [f"{heads_up} Ask me to fix it.", "It is noon."]
            await rig.speak("and now")  # still broken: not said again
            await rig.until(lambda: len(rig.ears.said) == 3)
            broken[0] = False
            await rig.speak("and again")  # working again
            await rig.until(lambda: len(rig.ears.said) == 4)
            await rig.speak("crash")  # broken again: said again
            await rig.until(lambda: len(rig.ears.said) == 6)
            assert rig.ears.said[4].startswith("Heads up: my voice check isn't working")
            assert len(rig.turns) == 4

    asyncio.run(run())


def _done(request_id: str) -> ChatDone:
    return ChatDone(request_id=request_id, model="m", stop_reason="end_turn", usage=Usage(), cost_usd=0,
                    latency_ms=1, first_token_ms=1, route="model", timings={})  # fmt: skip


def test_a_long_spoken_request_carries_on_while_the_conversation_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hands_free_later, "MOVE_ON_S", 0.3)

    async def run() -> None:
        rig = Rig(monkeypatch)
        release = asyncio.Event()

        async def turn(text: str, session: str | None, emit: Callable[[ServerMessage], Awaitable[None]],
                       speculation: object | None = None) -> str:  # fmt: skip
            rig.turns.append((text, session))
            if "report" in text:
                await emit(ChatDelta(request_id="r1", text="Building it now. "))
                await emit(
                    ToolStarted(request_id="r1", call_id="c1", tool="work", summary="Work", risk="write")
                )
                await release.wait()
                await emit(
                    ToolFinished(request_id="r1", call_id="c1", status="ok", preview="", duration_ms=1)
                )
                await emit(ChatDelta(request_id="r1", text="The report is built."))
                await emit(_done("r1"))
            else:
                await emit(ChatDelta(request_id="r2", text="It is noon."))
                await emit(_done("r2"))
            return "session-1"

        rig.hf._d.run_turn = turn
        async with rig.running():
            await rig.speak("jarvis build the report")
            await rig.until(lambda: hands_free_later.ON_IT in rig.ears.said)
            assert rig.ears.said == ["Building it now.", hands_free_later.ON_IT]
            await rig.speak("what time is it")  # no wake word: the conversation is still open
            await rig.until(lambda: "It is noon." in rig.ears.said)
            assert not release.is_set() and [t for t, _ in rig.turns] == [
                "build the report",
                "what time is it",
            ]
            release.set()
            await rig.until(lambda: rig.ears.said[-1:] == ["The report is built."])  # told when it's done

    asyncio.run(run())


def test_a_request_that_moved_on_asks_its_question_when_free_and_the_next_yes_answers_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hands_free_later, "MOVE_ON_S", 0.2)

    async def run() -> None:
        rig = Rig(monkeypatch)

        async def turn(text: str, session: str | None, emit: Callable[[ServerMessage], Awaitable[None]],
                       speculation: object | None = None) -> str:  # fmt: skip
            rig.turns.append((text, session))
            await asyncio.sleep(0.4)  # it has moved on by now
            ask = ApprovalRequested(
                approval_id="a1", request_id="r1", call_id="c1", tool="click_element", risk="purchase",
                summary="Pay ₹1,240 to TNPDCL", reason="", args={}, untrusted_context=False,
                can_remember=False, expires_in_s=120,
            )  # fmt: skip
            await emit(ask)
            await rig.until(lambda: ("a1", True) in rig.resolved)
            await emit(ApprovalResolved(approval_id="a1", approved=True, reason="you said yes"))
            await emit(ChatDelta(request_id="r1", text="Paid."))
            await emit(_done("r1"))
            return "session-1"

        rig.hf._d.run_turn = turn
        async with rig.running():
            await rig.speak("jarvis pay my eb bill")
            question = "This spends money: Pay ₹1,240 to TNPDCL. Go ahead?"
            await rig.until(lambda: question in rig.ears.said)
            rig.rec.text = ""
            await asyncio.sleep(0.1)  # once the question has been said
            await rig.speak("yes")  # no wake word needed: it's listening for the answer
            await rig.until(lambda: rig.ears.said[-1:] == ["Paid."])
            assert rig.resolved == [("a1", True)] and len(rig.turns) == 1  # the yes wasn't a new request

    asyncio.run(run())
