"""Claude via the official Anthropic SDK (the default provider)."""

from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

import anthropic

from contracts.model import (
    ChatMessage,
    Completed,
    ImageData,
    ModelError,
    ModelEvent,
    ModelRequest,
    ProviderStatus,
    Role,
    TextDelta,
    ThinkingStarted,
    ToolCallRequested,
    ToolResultBlock,
    Usage,
)
from models.config import AnthropicConfig, TierConfig
from models.pricing import estimate_cost

PROVIDER = "anthropic"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
STALE_REASONING = "bound to a different conversation"
"""Claude's 400 for a thinking block whose earlier conversation changed. Leaving every such block
out (a leading run) is always accepted, so that request is retried once without them."""
REASONING_BLOCKS = ("thinking", "redacted_thinking")
KEY_REJECTED = "Your Anthropic API key was rejected. Check the key in your .env file, then restart me."
ClientFactory = Callable[[str], anthropic.AsyncAnthropic]


class AnthropicProvider:
    name = PROVIDER

    def __init__(
        self,
        config: AnthropicConfig,
        api_key: Callable[[], str | None],
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._config = config
        self._api_key = api_key
        self._factory = client_factory or self._default_client
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_key: str | None = None

    def _default_client(self, key: str) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key=key, timeout=self._config.timeout_s, max_retries=self._config.max_retries
        )

    def _get_client(self) -> anthropic.AsyncAnthropic | None:
        key = self._api_key()
        if not key:
            return None
        if self._client is None or key != self._client_key:
            self._client = self._factory(key)
            self._client_key = key
        return self._client

    async def status(self) -> ProviderStatus:
        if not self._api_key():
            return ProviderStatus(provider=PROVIDER, ready=False, detail="No Anthropic API key set")
        return ProviderStatus(provider=PROVIDER, ready=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        client = self._get_client()
        if client is None:
            yield ModelError(code="no_api_key", message="Add your Anthropic API key in Settings.")
            return
        tier = self._config.tiers[request.tier]
        final = None
        try:
            for attempt in range(2):
                try:
                    async with self._open_stream(
                        client, tier, request, keep_reasoning=attempt == 0
                    ) as stream:
                        async for event in stream:
                            if event.type == "content_block_start" and event.content_block.type == "thinking":
                                yield ThinkingStarted()
                            elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                                yield TextDelta(text=event.delta.text)
                        final = await stream.get_final_message()
                    break
                except anthropic.BadRequestError as exc:
                    if attempt == 0 and STALE_REASONING in str(exc.body):
                        continue  # rejected before any output: again, without the earlier reasoning
                    raise
        except anthropic.AuthenticationError:
            yield ModelError(code="auth", message=KEY_REJECTED)
            return
        except anthropic.RateLimitError:
            yield ModelError(code="rate_limited", message="Anthropic rate limit hit.", retryable=True)
            return
        except anthropic.BadRequestError as exc:
            yield ModelError(code="bad_request", message=_error_text(exc))
            return
        except anthropic.APIStatusError as exc:
            code = "overloaded" if exc.status_code in (503, 529) else "provider_error"
            yield ModelError(code=code, message=_error_text(exc), retryable=exc.status_code >= 500)
            return
        except anthropic.APIConnectionError:
            yield ModelError(code="offline", message="Can't reach the Anthropic API.", retryable=True)
            return
        except ValueError:
            # With eager input streaming the SDK raises this for tool input it can't parse.
            yield ModelError(code="provider_error", message="The model produced a malformed tool call.")
            return
        assert final is not None

        # Every tool_use gets reported, even on max_tokens/refusal, so core can answer it
        # with a "not run" result; core only runs calls when stop_reason is tool_use.
        for block in final.content:
            if block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                yield ToolCallRequested(call_id=block.id, name=block.name, input=dict(args))
        usage = _usage(final.usage)
        model = str(final.model)
        yield Completed(
            provider=PROVIDER,
            model=model,
            stop_reason=str(final.stop_reason),
            usage=usage,
            cost_usd=estimate_cost(usage, self._config.prices.get(model)),
            provider_state={
                "provider": PROVIDER,
                "content": [b.model_dump(mode="json", exclude_none=True) for b in final.content],
            },
        )

    def _open_stream(
        self,
        client: anthropic.AsyncAnthropic,
        tier: TierConfig,
        request: ModelRequest,
        *,
        keep_reasoning: bool,
    ) -> Any:
        system: list[dict[str, Any]] = [
            {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
        ]
        if request.context:
            system.append({"type": "text", "text": request.context})
        kwargs: dict[str, Any] = {
            "model": tier.model,
            "max_tokens": tier.max_tokens,
            # Stable system prompt first, cached; top-level cache_control also caches history.
            "system": system,
            "cache_control": {"type": "ephemeral"},
            "messages": to_params(request.messages, keep_reasoning=keep_reasoning),
            "output_config": {"effort": tier.effort},
        }
        if request.tools:
            # Inputs stream as generated; core validates every input before running it.
            kwargs["tools"] = [{**t.model_dump(), "eager_input_streaming": True} for t in request.tools]
        if tier.refusal_fallback:
            return client.beta.messages.stream(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
        return client.messages.stream(**kwargs)


def to_params(messages: Iterable[ChatMessage], *, keep_reasoning: bool = True) -> list[dict[str, Any]]:
    """Convert history to API messages, merging consecutive same-role turns.

    Consecutive user turns happen when a turn failed or was cancelled before the reply.
    Tool results always come first in a user turn, as the API requires.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        blocks = _blocks(message)
        if not keep_reasoning:
            blocks = [b for b in blocks if b.get("type") not in REASONING_BLOCKS]
        if not blocks:
            continue
        role = message.role.value
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    return out


def _blocks(message: ChatMessage) -> list[dict[str, Any]]:
    state = message.provider_state
    if message.role is Role.ASSISTANT and state and state.get("provider") == PROVIDER:
        # Replay the full content (including thinking blocks) unchanged.
        return list(state["content"])
    blocks: list[dict[str, Any]] = [_tool_result(r) for r in message.tool_results]
    blocks.extend(_image(i) for i in message.images)  # images before the text that asks about them
    if message.text:
        blocks.append({"type": "text", "text": message.text})
    return blocks


def _image(image: ImageData) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": image.media_type, "data": image.data_b64},
    }


def _tool_result(result: ToolResultBlock) -> dict[str, Any]:
    content: str | list[dict[str, Any]] = result.output
    if result.images:
        content = [{"type": "text", "text": result.output}, *(_image(i) for i in result.images)]
    return {
        "type": "tool_result",
        "tool_use_id": result.call_id,
        "content": content,
        "is_error": result.is_error,
    }


def _usage(raw: Any) -> Usage:
    return Usage(
        input_tokens=raw.input_tokens or 0,
        output_tokens=raw.output_tokens or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", None) or 0,
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", None) or 0,
    )


def _error_text(exc: anthropic.APIStatusError) -> str:
    """Anthropic's own reason when it gave one ("credit balance is too low", "model: not found"),
    so a 400 says what to fix. It describes the request; it doesn't echo the key."""
    body = exc.body if isinstance(exc.body, dict) else {}
    inner = body.get("error")
    reason = inner.get("message") if isinstance(inner, dict) else None
    if not isinstance(reason, str) or not reason.strip():
        return f"Anthropic API error {exc.status_code}."
    reason = " ".join(reason.split())
    if "credit balance" in reason.lower():
        return "Your Anthropic account has no credit left. Add credit at console.anthropic.com → Billing."
    return f"Anthropic API error {exc.status_code}: {reason[:240]}"
