"""The agent loop for one chat turn: model -> tools -> model ... -> answer.

History stays append-only and valid at every point: an assistant turn that asked for
tools is stored together with its tool results, even when the turn is cancelled.
"""

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable

from contracts import new_trace_id
from contracts.context import ActiveContext
from contracts.model import (
    ChatMessage,
    Completed,
    ModelError,
    ModelEvent,
    ModelProvider,
    ModelRequest,
    ModelTier,
    Role,
    TextDelta,
    ThinkingStarted,
    ToolCallRequested,
    ToolResultBlock,
)
from contracts.skills import TraceStep
from contracts.ws import ChatCancelled, ChatDelta, ChatError, ChatSend, ChatStarted, ChatThinking
from core.chat_routes import RoutesMixin
from core.chat_types import Emit
from core.config import Settings
from core.prompts import SYSTEM_PROMPT
from core.router import plan
from core.router.rules import clean
from core.router.rules import match as match_rule
from core.skill_runner import SkillRunner
from core.speculation import Speculation
from core.speculation import start as start_speculation
from core.speech_text import is_yes_to
from core.store import Store
from core.tool_exec import ChatPermissions, ToolExecutor
from core.toolbox import ToolRegistry
from core.turn import Step, Turn

log = logging.getLogger(__name__)


class ChatService(RoutesMixin):
    def __init__(
        self,
        store: Store,
        provider: Callable[[], ModelProvider],
        settings: Callable[[], Settings],
        registry: ToolRegistry,
        executor: ToolExecutor,
        clock: Callable[[], float] = time.perf_counter,
        skills: SkillRunner | None = None,
        context: Callable[[str], Awaitable[str]] | None = None,
        active: Callable[[ChatPermissions], str] | None = None,
        on_turn: Callable[[str, str], None] | None = None,
    ) -> None:
        self._on_turn = on_turn
        self._active = active
        self._skills = skills
        self._context = context
        self._store = store
        self._provider = provider
        self._settings = settings
        self._registry = registry
        self._executor = executor
        self._clock = clock
        self._perms: dict[str, ChatPermissions] = {}

    async def run(
        self,
        send: ChatSend,
        emit: Emit,
        speculation: Speculation | None = None,
        background: bool = False,
        active: ActiveContext | None = None,
    ) -> None:
        """Run one chat turn. Cancelling the task stops everything and keeps partial work."""
        turn = Turn(trace_id=new_trace_id(), started=self._clock(), background=background)
        try:
            await self._run_turn(send, emit, turn, speculation, active)
        except asyncio.CancelledError:
            if turn.pending is not None:
                await self._flush(turn, "Not run: the user cancelled.")
            elif turn.parts and turn.session_id:
                partial = ChatMessage(role=Role.ASSISTANT, text="".join(turn.parts))
                await self._store.append_message(turn.session_id, turn.trace_id, partial)
            await self._store.audit("user", "chat.cancelled", {}, turn.trace_id)
            await emit(ChatCancelled(request_id=send.request_id))
        finally:
            if speculation is not None and not speculation.used:
                await self.discard(speculation)

    async def speculate(
        self, session_id: str | None, text: str, tier: ModelTier | None
    ) -> Speculation | None:
        """Start the model on a paused partial transcript (voice). None when not worth it."""
        settings = self._settings()
        cleaned = clean(text)
        if not settings.voice.speculative or not cleaned:
            return None
        if self._skills and settings.skills.enabled and await self._skills.find(cleaned):
            return None  # a skill would answer instantly anyway
        if settings.router.enabled and match_rule(cleaned, settings.router) is not None:
            return None
        history = await self._store.history(session_id) if session_id else []
        tier_ = tier or settings.default_tier
        messages = (*history, ChatMessage(role=Role.USER, text=text))
        request = ModelRequest(
            trace_id="speculation",
            system=SYSTEM_PROMPT,
            messages=messages,
            tier=tier_,
            tools=self._registry.specs(),
            context=await self._context(text) if self._context else "",
        )
        return start_speculation(
            self._provider(),
            request,
            text=text,
            session_id=session_id,
            history_len=len(history),
            now=self._clock(),
        )

    async def discard(self, spec: Speculation) -> None:
        spec.cancel()
        if spec.completed is not None:  # it finished before we threw it away: still paid for
            await self._store.record_cost("speculation", spec.completed)
        await self._store.audit("system", "speculation.discarded", {"paid": spec.completed is not None})

    async def permissions(self, session_id: str) -> ChatPermissions:
        perms = self._perms.get(session_id)
        if perms is None:
            perms = ChatPermissions(session_id=session_id, tainted=await self._store.is_tainted(session_id))
            self._perms[session_id] = perms
        return perms

    def forget_session(self, session_id: str) -> None:
        self._perms.pop(session_id, None)

    async def _run_turn(
        self,
        send: ChatSend,
        emit: Emit,
        turn: Turn,
        speculation: Speculation | None,
        active: ActiveContext | None,
    ) -> None:
        settings = self._settings()
        tier = send.tier or settings.default_tier
        session_id = await self._store.ensure_session(send.session_id, send.text)
        turn.session_id = session_id
        perms = await self.permissions(session_id)
        turn.tainted_before = perms.tainted
        perms.request = send.text
        if active is not None:  # the voice hotkey's snapshot: pinned to this chat from now on
            perms.context = active
            if active.selection or active.clipboard:
                perms.tainted = True  # text from another app is outside content
        cleaned = clean(send.text)
        history = await self._store.history(session_id)
        last_said = next((m.text for m in reversed(history) if m.role is Role.ASSISTANT and m.text), "")
        perms.said_yes, perms.paid_up_to = is_yes_to(last_said, send.text), None
        await emit(
            ChatStarted(
                request_id=send.request_id,
                trace_id=turn.trace_id,
                session_id=session_id,
                tier=tier,
                target=active.label() if active else None,
            )
        )
        user_msg = await self._user_message(send, emit, turn, perms)
        await self._store.append_message(session_id, turn.trace_id, user_msg)
        await self._store.audit(
            "user",
            "chat.request",
            {"tier": tier, "chars": len(send.text), "attach": send.attach},
            turn.trace_id,
        )

        direct_route = not send.force_model and send.attach == "none"
        if self._skills and direct_route and settings.skills.enabled:
            started = self._clock()
            found = await self._skills.find(cleaned)
            if found is not None:
                await self._run_skill(
                    send, emit, turn, found, perms, (self._clock() - started) * 1000, settings
                )
                return

        if direct_route and settings.router.enabled:
            started = self._clock()
            route = await plan(send.text, settings.router, self._registry)
            if route is not None:
                route_ms = (self._clock() - started) * 1000
                await self._instant(
                    send, emit, turn, route, perms, route_ms, settings.router.direct_without_asking
                )
                return

        messages = [*history, user_msg]
        specs = self._registry.specs()
        context = await self._context(send.text) if self._context else ""
        if self._active is not None and (where := self._active(perms)):
            context = f"{context}\n\n{where}" if context else where
        source: AsyncIterator[ModelEvent] | None = None
        if speculation is not None:
            if speculation.matches(send.text, send.session_id, len(history), tier, self._clock()):
                source = speculation.events()  # the model already started while the user paused
                turn.speculation = "used"
                if speculation.request is not None:  # carry on exactly as it started
                    messages, context = list(speculation.request.messages), speculation.request.context
            else:
                turn.speculation = "discarded"
        seen: Counter[tuple[str, str]] = Counter()
        limits = settings.agent
        if turn.background:  # a background task has its own, larger but hard budget
            bg = settings.background
            limits = limits.model_copy(update={"max_steps": bg.max_steps, "max_turn_tokens": bg.token_budget})
        for step_number in range(1, limits.max_steps + 1):
            request = ModelRequest(
                trace_id=turn.trace_id,
                system=SYSTEM_PROMPT,
                messages=tuple(messages),
                tier=tier,
                tools=specs,
                context=context,
            )
            stepped = await self._model_step(send, emit, turn, request, source)
            source = None
            if stepped is None:
                return
            done, calls = stepped
            assistant = ChatMessage(
                role=Role.ASSISTANT, text="".join(turn.parts), provider_state=done.provider_state
            )
            turn.parts = []
            if not calls:
                await self._store.append_message(session_id, turn.trace_id, assistant)
                note = None
                if self._skills and done.stop_reason == "end_turn" and send.attach == "none":
                    note = await self._skills.observe(
                        cleaned, turn.trace, turn.tainted_before, settings.skills, turn.trace_id
                    )
                await self._done(send, emit, turn, done.stop_reason, skill=note)
                if (
                    self._on_turn and not turn.background and perms.remote is None
                ):  # learn from what they said
                    self._on_turn(send.text, assistant.text)
                return
            turn.pending = Step(assistant, calls)
            if done.stop_reason != "tool_use":  # cut off (max_tokens) or declined: never run
                await self._flush(turn, "Not run: the reply ended before the call was complete.")
                await self._done(send, emit, turn, done.stop_reason)
                return
            looping = False
            for call in calls:
                key = (call.name, json.dumps(call.input, sort_keys=True, default=str))
                seen[key] += 1
                if seen[key] > limits.max_identical_calls:
                    looping = True
                    break
                outcome = await self._executor.execute(
                    call,
                    request_id=send.request_id,
                    trace_id=turn.trace_id,
                    perms=perms,
                    emit=emit,
                    background=turn.background,
                )
                turn.pending.results[call.call_id] = outcome.block
                turn.trace.append(
                    TraceStep(
                        tool=call.name,
                        args=call.input,
                        status=outcome.status,
                        risk=outcome.risk,
                        trust=outcome.trust,
                        evidence=outcome.evidence,
                    )
                )
            filler = (
                "Not run: the same call kept repeating, so the turn was stopped." if looping else "Not run."
            )
            messages.extend(await self._flush(turn, filler))
            if looping:
                await self._done(send, emit, turn, "loop_detected")
                return
            tier = self._maybe_escalate(turn, tier, step_number, settings)
            if turn.total_tokens > limits.max_turn_tokens:
                await self._done(send, emit, turn, "budget")
                return
        await self._done(send, emit, turn, "step_limit")

    async def _model_step(
        self,
        send: ChatSend,
        emit: Emit,
        turn: Turn,
        request: ModelRequest,
        source: AsyncIterator[ModelEvent] | None = None,
    ) -> tuple[Completed, list[ToolCallRequested]] | None:
        calls: list[ToolCallRequested] = []
        async for event in source or self._provider().stream(request):
            if isinstance(event, TextDelta):
                if turn.first_token_ms is None:
                    turn.first_token_ms = (self._clock() - turn.started) * 1000
                turn.parts.append(event.text)
                await emit(ChatDelta(request_id=send.request_id, text=event.text))
            elif isinstance(event, ThinkingStarted):
                await emit(ChatThinking(request_id=send.request_id))
            elif isinstance(event, ToolCallRequested):
                calls.append(event)
            elif isinstance(event, Completed):
                turn.add_usage(event)
                await self._store.record_cost(turn.trace_id, event)
                return event, calls
            elif isinstance(event, ModelError):
                await self._store.audit("system", "chat.error", {"code": event.code}, turn.trace_id)
                await emit(ChatError(request_id=send.request_id, code=event.code, message=event.message))
                return None
        log.error("provider stream ended without a terminal event (trace %s)", turn.trace_id)
        await emit(
            ChatError(
                request_id=send.request_id,
                code="provider_error",
                message="The model stream ended unexpectedly.",
            )
        )
        return None

    async def _flush(self, turn: Turn, filler: str) -> list[ChatMessage]:
        """Store the pending tool-calling turn with a result for every call."""
        step, turn.pending = turn.pending, None
        if step is None or turn.session_id is None:
            return []
        blocks = tuple(
            step.results.get(c.call_id) or ToolResultBlock(call_id=c.call_id, output=filler, is_error=True)
            for c in step.calls
        )
        results = ChatMessage(role=Role.USER, text="", tool_results=blocks)
        await self._store.append_messages(turn.session_id, turn.trace_id, step.assistant, results)
        return [step.assistant, results]
