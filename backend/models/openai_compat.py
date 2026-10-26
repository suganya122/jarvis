"""Optional free local fallback: any server speaking the OpenAI-compatible chat API.

Covers Ollama, LM Studio, llama.cpp server and vLLM with one adapter. Local
models cost nothing, so cost is always 0.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from contracts.model import (
    ChatMessage,
    Completed,
    ModelError,
    ModelEvent,
    ModelRequest,
    ProviderStatus,
    TextDelta,
    Usage,
)
from models.config import LocalConfig

PROVIDER = "local"


class OpenAICompatProvider:
    name = PROVIDER

    def __init__(self, config: LocalConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._config = config
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=str(self._config.base_url).rstrip("/"),
            timeout=self._config.timeout_s,
            transport=self._transport,
        )

    async def status(self) -> ProviderStatus:
        try:
            async with self._client() as client:
                response = await client.get("/models", timeout=3.0)
            ready = response.status_code == 200
            return ProviderStatus(provider=PROVIDER, ready=ready, detail=f"HTTP {response.status_code}")
        except httpx.HTTPError:
            return ProviderStatus(provider=PROVIDER, ready=False, detail="Local model server not reachable")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        body = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            # Local servers get text only; tool use is a Claude feature for now.
            "messages": [{"role": "system", "content": _system(request)}]
            + [{"role": m.role.value, "content": _text(m)} for m in request.messages if _text(m)],
        }
        usage = Usage()
        stop_reason = "end_turn"
        try:
            async with (
                self._client() as client,
                client.stream("POST", "/chat/completions", json=body) as response,
            ):
                if response.status_code != 200:
                    yield ModelError(
                        code="provider_error",
                        message=f"Local model server returned HTTP {response.status_code}.",
                    )
                    return
                async for line in response.aiter_lines():
                    chunk = _parse_sse(line)
                    if chunk is None:
                        continue
                    if chunk.get("usage"):
                        usage = Usage(
                            input_tokens=chunk["usage"].get("prompt_tokens", 0),
                            output_tokens=chunk["usage"].get("completion_tokens", 0),
                        )
                    for choice in chunk.get("choices", []):
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            yield TextDelta(text=text)
                        if choice.get("finish_reason") == "length":
                            stop_reason = "max_tokens"
        except httpx.ConnectError:
            yield ModelError(code="offline", message="Local model server is not running.", retryable=True)
            return
        except httpx.HTTPError as exc:
            yield ModelError(code="provider_error", message=f"Local model error: {type(exc).__name__}")
            return
        yield Completed(
            provider=PROVIDER,
            model=self._config.model,
            stop_reason=stop_reason,
            usage=usage,
            cost_usd=0.0,
        )


def _text(message: ChatMessage) -> str:
    results = "\n".join(f"[tool result] {r.output}" for r in message.tool_results)
    return "\n".join(part for part in (results, message.text) if part)


def _parse_sse(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _system(request: ModelRequest) -> str:
    return "\n\n".join(part for part in (request.system, request.context) if part)
