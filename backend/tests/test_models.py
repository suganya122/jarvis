from types import SimpleNamespace as NS
from typing import Any

import anthropic
import httpx
import pytest

from contracts.model import (
    ChatMessage,
    Completed,
    ModelError,
    ModelRequest,
    ModelTier,
    Role,
    TextDelta,
    ThinkingStarted,
    ToolResultBlock,
    ToolSpec,
    Usage,
)
from models.anthropic_provider import FALLBACK_BETA, AnthropicProvider, to_params
from models.config import AnthropicConfig, LocalConfig, Price
from models.openai_compat import OpenAICompatProvider
from models.pricing import estimate_cost


def request(tier: ModelTier = ModelTier.CHEAP, history: tuple[ChatMessage, ...] = ()) -> ModelRequest:
    return ModelRequest(
        trace_id="t",
        system="sys",
        messages=(*history, ChatMessage(role=Role.USER, text="hi")),
        tier=tier,
    )


async def collect(provider: Any, req: ModelRequest) -> list[Any]:
    return [event async for event in provider.stream(req)]


# ---- pricing -------------------------------------------------------------------


def test_pricing_includes_cache_tokens() -> None:
    price = Price(input=1, output=10, cache_read=0.1, cache_write=1.25)
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    assert estimate_cost(usage, price) == pytest.approx(1 + 1 + 0.1)
    assert estimate_cost(usage, None) == 0


def test_pricing_long_context_rates() -> None:
    price = AnthropicConfig().prices["claude-haiku-5-5"]
    short = estimate_cost(Usage(input_tokens=100_000, output_tokens=0), price)
    long = estimate_cost(Usage(input_tokens=100_001, output_tokens=0), price)
    assert short == pytest.approx(0.01)
    assert long == pytest.approx(100_001 * 0.50 / 1e6)


# ---- anthropic -------------------------------------------------------------------


class FakeStream:
    def __init__(self, events: list[Any], final: Any) -> None:
        self._events = events
        self._final = final

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for e in self._events:
                yield e

        return gen()

    async def get_final_message(self) -> Any:
        return self._final


class FakeBlock:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return dict(self.__dict__)


def fake_client(calls: list[tuple[str, dict[str, Any]]]) -> Any:
    events = [
        NS(type="content_block_start", content_block=NS(type="thinking")),
        NS(type="content_block_delta", delta=NS(type="thinking_delta", thinking="")),
        NS(type="content_block_delta", delta=NS(type="text_delta", text="Hel")),
        NS(type="content_block_delta", delta=NS(type="text_delta", text="lo")),
    ]
    final = NS(
        model="claude-haiku-5-5",
        stop_reason="end_turn",
        content=[
            FakeBlock(type="thinking", thinking="", signature="sig"),
            FakeBlock(type="text", text="Hello"),
        ],
        usage=NS(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=5000,
            cache_creation_input_tokens=None,
        ),
    )

    def stream_for(kind: str) -> Any:
        def stream(**kwargs: Any) -> FakeStream:
            calls.append((kind, kwargs))
            return FakeStream(events, final)

        return stream

    return NS(
        messages=NS(stream=stream_for("messages")),
        beta=NS(messages=NS(stream=stream_for("beta"))),
    )


async def test_anthropic_stream_maps_events_and_cost() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    provider = AnthropicProvider(
        AnthropicConfig(), api_key=lambda: "k", client_factory=lambda _k: fake_client(calls)
    )
    events = await collect(provider, request())
    assert isinstance(events[0], ThinkingStarted)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    done = events[-1]
    assert isinstance(done, Completed)
    assert done.usage == Usage(input_tokens=1000, output_tokens=200, cache_read_tokens=5000)
    assert done.cost_usd == pytest.approx((1000 * 0.10 + 200 * 0.50 + 5000 * 0.01) / 1e6)
    assert done.provider_state is not None
    assert done.provider_state["content"][0]["signature"] == "sig"

    kind, kwargs = calls[0]
    assert kind == "messages"
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["output_config"] == {"effort": "low"}


async def test_anthropic_balanced_tier_uses_server_side_fallback() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    provider = AnthropicProvider(
        AnthropicConfig(), api_key=lambda: "k", client_factory=lambda _k: fake_client(calls)
    )
    await collect(provider, request(ModelTier.BALANCED))
    kind, kwargs = calls[0]
    assert kind == "beta"
    assert kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["fallbacks"] == "default"


async def test_anthropic_replays_assistant_content_verbatim() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    provider = AnthropicProvider(
        AnthropicConfig(), api_key=lambda: "k", client_factory=lambda _k: fake_client(calls)
    )
    blocks = [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "yo"}]
    history = (
        ChatMessage(role=Role.USER, text="first"),
        ChatMessage(
            role=Role.ASSISTANT, text="yo", provider_state={"provider": "anthropic", "content": blocks}
        ),
    )
    await collect(provider, request(history=history))
    messages = calls[0][1]["messages"]
    assert messages[0] == {"role": "user", "content": [{"type": "text", "text": "first"}]}
    assert messages[1] == {"role": "assistant", "content": blocks}


def test_to_params_merges_turns_and_orders_tool_results_first() -> None:
    tool_turn = {
        "provider": "anthropic",
        "content": [{"type": "tool_use", "id": "c1", "name": "t", "input": {}}],
    }
    history = [
        ChatMessage(role=Role.USER, text="a"),
        ChatMessage(role=Role.USER, text="b"),  # previous turn failed before a reply
        ChatMessage(role=Role.ASSISTANT, text="", provider_state=tool_turn),
        ChatMessage(role=Role.USER, text="", tool_results=(ToolResultBlock(call_id="c1", output="ok"),)),
        ChatMessage(role=Role.USER, text="next"),
    ]
    params = to_params(history)
    assert [p["role"] for p in params] == ["user", "assistant", "user"]
    assert [b["text"] for b in params[0]["content"]] == ["a", "b"]
    assert params[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": "ok",
        "is_error": False,
    }
    assert params[2]["content"][1] == {"type": "text", "text": "next"}


async def test_anthropic_sends_tools_with_eager_streaming() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    provider = AnthropicProvider(
        AnthropicConfig(), api_key=lambda: "k", client_factory=lambda _k: fake_client(calls)
    )
    spec = ToolSpec(name="t", description="d", input_schema={"type": "object"})
    req = ModelRequest(
        trace_id="t",
        system="s",
        messages=(ChatMessage(role=Role.USER, text="hi"),),
        tier=ModelTier.CHEAP,
        tools=(spec,),
    )
    await collect(provider, req)
    assert calls[0][1]["tools"] == [
        {"name": "t", "description": "d", "input_schema": {"type": "object"}, "eager_input_streaming": True}
    ]


async def test_anthropic_without_key_reports_no_api_key() -> None:
    provider = AnthropicProvider(AnthropicConfig(), api_key=lambda: None)
    events = await collect(provider, request())
    assert len(events) == 1 and isinstance(events[0], ModelError)
    assert events[0].code == "no_api_key"
    assert not (await provider.status()).ready


# ---- local OpenAI-compatible ----------------------------------------------------


def sse_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": []})
    body = (
        'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" there"},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


async def test_local_provider_streams_sse() -> None:
    provider = OpenAICompatProvider(LocalConfig(), transport=httpx.MockTransport(sse_handler))
    events = await collect(provider, request())
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hi", " there"]
    done = events[-1]
    assert isinstance(done, Completed)
    assert done.cost_usd == 0 and done.usage.input_tokens == 7
    assert (await provider.status()).ready


async def test_local_provider_offline() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = OpenAICompatProvider(LocalConfig(), transport=httpx.MockTransport(refuse))
    events = await collect(provider, request())
    assert isinstance(events[-1], ModelError) and events[-1].code == "offline"
    assert not (await provider.status()).ready


async def test_reasoning_claude_no_longer_accepts_is_left_out_and_the_request_retried() -> None:
    """A thinking block whose earlier conversation changed is a 400; the same request without the
    earlier reasoning is always accepted, so the person sees an answer, not an API error."""
    import httpx2

    calls: list[tuple[str, dict[str, Any]]] = []
    client = fake_client(calls)
    working = client.messages.stream

    def first_fails(**kwargs: Any) -> FakeStream:
        if not calls:
            calls.append(("rejected", kwargs))
            body = {"error": {"message": "messages.3.content.0: Invalid `signature` in `thinking` block. "
                              "The block is bound to a different conversation."}}  # fmt: skip
            response = httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com"))
            raise anthropic.BadRequestError("bad", response=response, body=body)
        return working(**kwargs)

    client.messages.stream = first_fails
    provider = AnthropicProvider(AnthropicConfig(), api_key=lambda: "k", client_factory=lambda _k: client)
    blocks = [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "yo"}]
    history = (
        ChatMessage(role=Role.USER, text="first"),
        ChatMessage(
            role=Role.ASSISTANT, text="yo", provider_state={"provider": "anthropic", "content": blocks}
        ),
    )
    events = await collect(provider, request(history=history))
    assert isinstance(events[-1], Completed)
    assert calls[0][1]["messages"][1]["content"] == blocks
    assert calls[1][1]["messages"][1]["content"] == [{"type": "text", "text": "yo"}]
