"""Speculative start, dictation clean-up, and the voice hub (no audio needed)."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from contracts.model import ModelTier
from contracts.voice import TranscriptEvent
from contracts.ws import ChatSend, ServerMessage, VoiceStart, VoiceStop, VoiceTiming
from core import db
from core.config import Settings
from core.dictation import keeps_content, local_cleanup
from core.redact import Redactor
from core.runtime import Runtime
from core.secret_store import MemoryBackend, SecretStore
from core.store import Store
from models.fake import FakeProvider, FakeStep
from tests.tools_fixture import make_tools

OPEN: list[Store] = []


async def runtime(tmp_path: Path, provider: FakeProvider, settings: Settings | None = None) -> Runtime:
    store = Store(await db.connect(tmp_path / f"t{len(OPEN)}.sqlite3"), Redactor())
    OPEN.append(store)
    return Runtime(
        store,
        SecretStore(MemoryBackend(), env={}),
        settings or Settings(),
        Redactor(),
        data_dir=tmp_path,
        provider_override=provider,
        extra_tools=tuple(make_tools().values()),
    )


@pytest.fixture(autouse=True)
async def close_stores() -> AsyncIterator[None]:
    yield
    while OPEN:
        await OPEN.pop().close()


class Sink:
    def __init__(self) -> None:
        self.events: list[ServerMessage] = []

    async def __call__(self, message: ServerMessage) -> None:
        self.events.append(message)

    def of(self, kind: str) -> list[Any]:
        return [e for e in self.events if e.type == kind]


def send(text: str, stream: str = "v1") -> ChatSend:
    return ChatSend(request_id="r1", text=text, voice=VoiceTiming(stream_id=stream, release_to_final_ms=40))


# ---- speculation ------------------------------------------------------------------


async def test_matching_final_reuses_the_speculative_call(tmp_path: Path) -> None:
    provider = FakeProvider(reply="Here is a long answer.", delay_s=0.01)
    rt = await runtime(tmp_path, provider)
    spec = await rt.chat.speculate(None, "tell me about rust", ModelTier.CHEAP)
    assert spec is not None
    sink = Sink()
    await rt.chat.run(send("Tell me about Rust"), sink, spec)  # same words: case/punctuation don't matter
    assert len(provider.requests) == 1
    done = sink.of("chat.done")[0]
    assert done.timings["speculation_used"] == 1.0
    assert "".join(e.text for e in sink.of("chat.delta")) == "Here is a long answer."


async def test_changed_final_discards_the_speculation(tmp_path: Path) -> None:
    provider = FakeProvider(reply="ok")
    rt = await runtime(tmp_path, provider)
    spec = await rt.chat.speculate(None, "tell me about rust", ModelTier.CHEAP)
    assert spec is not None
    await asyncio.sleep(0.05)  # let the speculative call finish, so its cost is real
    sink = Sink()
    await rt.chat.run(send("tell me about rust macros"), sink, spec)
    assert len(provider.requests) == 2
    assert sink.of("chat.done")[0].timings["speculation_used"] == 0.0
    audit = [e.action for e in await rt.store.list_audit()]
    assert "speculation.discarded" in audit
    assert (await rt.store.cost_summary()).today_tokens > 0  # discarded but still paid for: recorded


async def test_an_adopted_start_carries_on_with_exactly_what_it_sent(tmp_path: Path) -> None:
    """The steps after a tool call resend the conversation; Claude only accepts the first step's
    reasoning back if everything before it is byte-identical, so the words as heard during the pause
    and the context gathered then are kept, not the final transcript's capitals or a new context."""
    provider = FakeProvider(script=[FakeStep(tool_calls=[("peek", {"value": "x"})]), FakeStep(text="done")])
    rt = await runtime(tmp_path, provider)
    spec = await rt.chat.speculate(None, "peek at the notes", ModelTier.CHEAP)
    assert spec is not None
    await rt.chat.run(send("Peek at the notes."), Sink(), spec)
    first, second = provider.requests
    assert second.messages[: len(first.messages)] == first.messages
    assert second.context == first.context


async def test_speculation_never_runs_tools_before_the_final(tmp_path: Path) -> None:
    provider = FakeProvider(script=[FakeStep(tool_calls=[("note", {"value": "x"})]), FakeStep(text="done")])
    rt = await runtime(tmp_path, provider)
    note = rt.registry.get("note")
    spec = await rt.chat.speculate(None, "save a note", ModelTier.CHEAP)
    assert spec is not None
    await asyncio.sleep(0.05)
    assert note.runs == []  # type: ignore[union-attr]  # the model asked for a tool; nothing ran
    await rt.chat.discard(spec)
    assert note.runs == []  # type: ignore[union-attr]


async def test_no_speculation_for_instant_commands_or_when_off(tmp_path: Path) -> None:
    rt = await runtime(tmp_path, FakeProvider())
    assert await rt.chat.speculate(None, "what time is it", ModelTier.CHEAP) is None
    settings = Settings()
    settings.voice.speculative = False
    rt_off = await runtime(tmp_path, FakeProvider(), settings)
    assert await rt_off.chat.speculate(None, "tell me a story", ModelTier.CHEAP) is None


# ---- dictation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("um so i think we should uh meet tomorrow", "So I think we should meet tomorrow."),
        ("hello comma world period", "Hello, world."),
        ("email me at john at example dot com", "Email me at john@example.com."),
        ("first line new line second line", "First line\nSecond line."),
    ],
)
def test_local_cleanup(raw: str, clean: str) -> None:
    assert local_cleanup(raw) == clean


def test_guard_rejects_dropped_or_invented_content() -> None:
    raw = "please book the meeting room for tuesday at three and invite the design team"
    assert keeps_content(raw, "Please book the meeting room for Tuesday at 3 and invite the design team.")
    assert not keeps_content(raw, "Please book the meeting room.")
    assert not keeps_content(
        "call bob", "Call Bob. " + "Also here is a long unrelated paragraph the model made up. " * 3
    )
    assert keeps_content("meet at five no six", "Meet at 6.")


async def test_dictation_uses_llm_then_falls_back(tmp_path: Path) -> None:
    good = await runtime(tmp_path, FakeProvider(reply="Meet at 6 tomorrow."))
    text, method, _ = await good.dictation.clean("um meet at five no six tomorrow", "t")
    assert (text, method) == ("Meet at 6 tomorrow.", "llm")
    lossy = await runtime(tmp_path, FakeProvider(reply="Meet."))
    text, method, note = await lossy.dictation.clean("please meet the design team tomorrow at noon", "t")
    assert method == "local" and "exact words" in note and "design team" in text


async def test_dictation_types_into_the_focused_app(tmp_path: Path) -> None:
    from contracts.common import Capability, RiskLevel
    from tests.tools_fixture import FakeTool

    rt = await runtime(tmp_path, FakeProvider(reply="Hello there."))
    tool = FakeTool("insert_text", RiskLevel.WRITE, (Capability.INPUT_CONTROL,))
    rt.registry.register(tool)
    sink = Sink()
    await rt.dictation.run("v9", "hello there", "dictate_app", sink)
    result = sink.of("dictation.result")[0]
    assert result.inserted and result.method == "llm" and tool.runs == [{"text": "Hello there."}]
    assert not sink.of("approval.request")  # pressing the dictation key is a direct request


# ---- voice hub --------------------------------------------------------------------


class FakeWorker:
    alive = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def audio_open(self, stream_id: str, rate: int) -> None:
        self.calls.append(("open", stream_id))

    async def audio_chunk(self, stream_id: str, pcm: str) -> None:
        self.calls.append(("chunk", stream_id))

    async def audio_close(self, stream_id: str, abort: bool = False) -> None:
        self.calls.append(("close", stream_id, str(abort)))


async def test_hub_streams_partials_speculates_on_pause_and_hands_over(tmp_path: Path) -> None:
    provider = FakeProvider(reply="answer", delay_s=0.01)
    rt = await runtime(tmp_path, provider)
    worker = FakeWorker()
    rt.voice._worker = lambda: worker  # type: ignore[assignment]
    sink = Sink()
    await rt.voice.start(sink, VoiceStart(stream_id="v1"))
    rt.voice.on_transcript(TranscriptEvent(stream_id="v1", kind="partial", text="tell me"))
    rt.voice.on_transcript(TranscriptEvent(stream_id="v1", kind="segment", text="tell me about rust"))
    await asyncio.sleep(0.05)
    await rt.voice.stop(VoiceStop(stream_id="v1"))
    rt.voice.on_transcript(TranscriptEvent(stream_id="v1", kind="final", text="tell me about rust"))
    await asyncio.sleep(0.05)
    assert [e.text for e in sink.of("voice.partial")] == ["tell me", "tell me about rust"]
    assert sink.of("voice.final")[0].text == "tell me about rust"
    spec = rt.voice.take_speculation("v1")
    assert spec is not None and len(provider.requests) == 1
    await rt.chat.run(send("tell me about rust"), sink, spec)
    assert len(provider.requests) == 1  # the paused partial's call was reused


async def test_hub_without_engine_explains(tmp_path: Path) -> None:
    rt = await runtime(tmp_path, FakeProvider())
    sink = Sink()
    await rt.voice.start(sink, VoiceStart(stream_id="v1"))
    assert "speech model" in sink.of("voice.error")[0].message


def test_an_early_start_before_the_chat_was_saved_still_counts() -> None:
    """Hands-free: the model starts during a pause before the conversation has a saved chat;
    the chat is created a moment later. Both are the same empty conversation."""
    from contracts.model import ModelTier
    from core.speculation import Speculation

    early = Speculation(
        text="what time is it", session_id=None, history_len=0, tier=ModelTier.CHEAP, started=0.0
    )
    assert early.matches("What time is it?", "new-session", 0, ModelTier.CHEAP, now=1.0)
    with_history = Speculation(
        text="and tomorrow", session_id=None, history_len=2, tier=ModelTier.CHEAP, started=0.0
    )
    assert not with_history.matches(
        "and tomorrow", "s1", 2, ModelTier.CHEAP, now=1.0
    )  # a real chat must match
    assert not early.matches("what time is it now", "new-session", 0, ModelTier.CHEAP, now=1.0)  # other words
