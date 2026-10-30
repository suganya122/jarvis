"""Building an ability: the most capable model writes it, then it is installed and test-run, and any
failure goes back to the model to fix (a few rounds). Nothing is saved unless the test passed."""

import json
import platform
import re
import shutil
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts.model import (
    ChatMessage,
    Completed,
    ModelError,
    ModelProvider,
    ModelRequest,
    ModelTier,
    Role,
    TextDelta,
)
from core.abilities import runner
from core.abilities.spec import DECLARABLE, MAX_PACKAGES, AbilitySpec, check, save

ROUNDS = 3
IMPOSSIBLE = "impossible: "
SYSTEM = f"""You write abilities for a personal assistant running on the user's own computer
({platform.system()} {platform.machine()}, Python {sys.version_info.major}.{sys.version_info.minor}).
An ability is one Python file that the assistant can call as a tool from then on. Write it so it
works the first time, for this computer, and does exactly what was asked, nothing more.

Reply with a JSON object, then the code in one ```python block, and nothing else:
{{"name": "short_snake_case_name",
  "description": "What it does and when to use it, for the assistant (1-2 sentences).",
  "input_schema": {{"type": "object", "properties": {{…}}, "required": […]}},
  "risk": "write" | "external_send" | "destructive" | "purchase",
  "capabilities": [...],
  "packages": [...],
  "test_args": {{…}}}}

- main.py defines `def run(args: dict) -> str`, returning a short plain-text result for the
  assistant to read out. Raise an exception with a clear message when something is wrong.
- "risk": external_send if it sends anything to other people, destructive if it deletes or
  overwrites the user's things, purchase if it can spend money; otherwise write.
- "capabilities": any of {", ".join(sorted(c.value for c in DECLARABLE))} that it uses.
- "packages": PyPI packages it needs (at most {MAX_PACKAGES}); prefer the standard library,
  the computer's own tools (on macOS: osascript, say, open, shortcuts, mdfind), or free web
  services that need no key. Never ask for or embed API keys, passwords or tokens.
- "test_args": arguments for a harmless test run that proves it works. It runs straight
  away, so it must not send, delete, buy, or change anything that matters.
- It runs in its own process with a 2 minute limit and no access to the assistant's secrets.
If what was asked isn't possible at all, reply only {{"impossible": "why, in one sentence"}}."""

FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Built:
    spec: AbilitySpec | None
    message: str
    """What happened: the test result, or why it couldn't be built."""


def parse(text: str) -> tuple[dict[str, Any], str] | str:
    start = text.find("{")
    if start < 0:
        return "reply with the JSON object first"
    try:
        manifest, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return "the JSON object didn't parse"
    if not isinstance(manifest, dict):
        return "the JSON must be an object"
    if "impossible" in manifest:
        return f"{IMPOSSIBLE}{manifest['impossible']}"
    code = FENCE.search(text[start:])
    return (manifest, code.group(1)) if code else "the code must follow in one ```python block"


class Builder:
    def __init__(
        self,
        provider: Callable[[], ModelProvider],
        record_cost: Callable[[str, Completed], Awaitable[None]],
        folder: Path,
    ) -> None:
        self._provider = provider
        self._record_cost = record_cost
        self._folder = folder

    async def build(self, goal: str, details: str, taken: set[str]) -> Built:
        messages = [
            ChatMessage(role=Role.USER, text=f"Build an ability that will: {goal}\n\n{details}".strip())
        ]
        problem = ""
        for _ in range(ROUNDS):
            reply = await self._ask(messages)
            if reply is None:
                return Built(None, "the model couldn't be reached")
            messages.append(ChatMessage(role=Role.ASSISTANT, text=reply))
            spec, problem = await self._try(reply, goal, taken)
            if spec is not None:
                return Built(spec, problem)
            if problem.startswith(IMPOSSIBLE):
                return Built(None, problem)
            messages.append(
                ChatMessage(
                    role=Role.USER, text=f"That didn't work: {problem}\nFix it and reply again in full."
                )
            )
        return Built(None, f"it still failed after {ROUNDS} tries: {problem}")

    async def _try(self, reply: str, goal: str, taken: set[str]) -> tuple[AbilitySpec | None, str]:
        parsed = parse(reply)
        if isinstance(parsed, str):
            return None, parsed
        manifest, code = parsed
        manifest["created_at"] = time.time()
        spec = check(manifest, code, goal)
        if isinstance(spec, str):
            return None, spec
        if spec.name in taken:
            return None, f'the name "{spec.name}" is taken; choose another'
        staging = self._folder / f".building-{uuid.uuid4().hex[:8]}"
        try:
            save(spec, staging)
            installed = await runner.install(spec.packages, staging)
            if not installed.ok:
                return None, f"installing {', '.join(spec.packages)} failed: {installed.output}"
            ran = await runner.run(staging, spec.test_args)
            if not ran.ok:
                return None, f"the test run failed: {ran.output}"
            final = self._folder / spec.name
            shutil.rmtree(final, ignore_errors=True)
            staging.rename(final)
            return spec, ran.output
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def _ask(self, messages: list[ChatMessage]) -> str | None:
        request = ModelRequest(
            trace_id=uuid.uuid4().hex, system=SYSTEM, messages=tuple(messages), tier=ModelTier.MAX
        )
        parts: list[str] = []
        async for event in self._provider().stream(request):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, Completed):
                await self._record_cost("ability", event)
            elif isinstance(event, ModelError):
                return None
        return "".join(parts)
