"""Deterministic provider for tests and for UI development without an API key."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

from contracts.model import (
    Completed,
    ModelError,
    ModelEvent,
    ModelRequest,
    ProviderStatus,
    TextDelta,
    ToolCallRequested,
    Usage,
)

PROVIDER = "fake"


@dataclass
class FakeStep:
    """One scripted model reply: optional text, then optional tool calls."""

    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stop_reason: str | None = None


class FakeProvider:
    name = PROVIDER

    def __init__(
        self,
        reply: str | None = None,
        delay_s: float = 0.0,
        error: ModelError | None = None,
        script: Iterable[FakeStep] = (),
    ) -> None:
        self._reply = reply
        self._delay_s = delay_s
        self._error = error
        self._script = list(script)
        self._calls = 0
        self.requests: list[ModelRequest] = []

    def _default_step(self, request: ModelRequest) -> FakeStep:
        last = request.messages[-1]
        if last.tool_results:
            return FakeStep(text=f"Tool finished: {last.tool_results[0].output[:2000]}")
        # Dev convenience for trying tools without an API key: "tool: list_directory {"path": "~"}"
        if last.text.startswith("tool:"):
            name, _, raw = last.text[5:].strip().partition(" ")
            try:
                args = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                args = {}
            return FakeStep(
                text=f"Calling {name}.", tool_calls=[(name, args if isinstance(args, dict) else {})]
            )
        return FakeStep(text=self._reply if self._reply is not None else f"echo: {last.text}")

    async def status(self) -> ProviderStatus:
        return ProviderStatus(provider=PROVIDER, ready=True, detail="Fake model (no API calls)")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if self._error is not None:
            yield self._error
            return
        step = self._script.pop(0) if self._script else self._default_step(request)
        words = step.text.split(" ") if step.text else []
        for i, word in enumerate(words):
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield TextDelta(text=word if i == 0 else " " + word)
        for name, args in step.tool_calls:
            self._calls += 1
            yield ToolCallRequested(call_id=f"call_{self._calls}", name=name, input=args)
        yield Completed(
            provider=PROVIDER,
            model="fake-model",
            stop_reason=step.stop_reason or ("tool_use" if step.tool_calls else "end_turn"),
            usage=Usage(input_tokens=len(request.messages), output_tokens=max(1, len(words))),
            cost_usd=0.0,
        )
