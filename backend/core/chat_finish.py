"""How a chat turn moves up a tier and how it finishes (chat.done, audit, timing log).

Split from chat.py to keep files small; ChatService mixes these in.
"""

import logging
from collections.abc import Callable
from typing import Literal

from contracts.model import ModelTier
from contracts.skills import SkillNote
from contracts.ws import ChatDone, ChatSend
from core.chat_types import Emit
from core.config import Settings
from core.store import Store
from core.turn import Turn

log = logging.getLogger("core.chat")


class FinishMixin:
    _store: Store
    _clock: Callable[[], float]

    def _maybe_escalate(self, turn: Turn, tier: ModelTier, step: int, settings: Settings) -> ModelTier:
        """Cheap first; move up once, for the rest of the turn, when it isn't enough."""
        if tier is not ModelTier.CHEAP or turn.escalated or not settings.agent.escalate:
            return tier
        if any(s.status == "error" for s in turn.trace):
            turn.escalated = "a step failed"
        elif step >= settings.agent.escalate_after_steps:
            turn.escalated = "multi-step task"
        else:
            return tier
        log.info("trace=%s escalating to balanced: %s", turn.trace_id, turn.escalated)
        return ModelTier.BALANCED

    async def _done(
        self,
        send: ChatSend,
        emit: Emit,
        turn: Turn,
        stop_reason: str,
        route: Literal["instant", "skill", "model"] = "model",
        timings: dict[str, float] | None = None,
        rule: str | None = None,
        skill: SkillNote | None = None,
    ) -> None:
        latency_ms = (self._clock() - turn.started) * 1000
        timings = {**(timings or {}), **voice_timings(send, turn, route)}
        detail = {"model": turn.model, "stop_reason": stop_reason, "cost_usd": turn.cost_usd, "route": route}
        if rule:
            detail["rule"] = rule
        if turn.escalated:
            detail["escalated"] = turn.escalated
        await self._store.audit("assistant", "chat.result", detail, turn.trace_id)
        log.info(
            "trace=%s route=%s model=%s timings=%s first_token_ms=%s total_ms=%.0f cost=$%.6f",
            turn.trace_id,
            rule or route,
            turn.model,
            timings or {},
            f"{turn.first_token_ms:.0f}" if turn.first_token_ms is not None else "-",
            latency_ms,
            turn.cost_usd,
        )
        await emit(
            ChatDone(
                request_id=send.request_id,
                model=turn.model,
                stop_reason=stop_reason,
                usage=turn.usage,
                cost_usd=round(turn.cost_usd, 8),
                latency_ms=round(latency_ms, 1),
                first_token_ms=round(turn.first_token_ms, 1) if turn.first_token_ms is not None else None,
                route=route,
                timings=timings or {},
                skill=skill,
                escalated=turn.escalated,
            )
        )


def voice_timings(send: ChatSend, turn: Turn, route: str) -> dict[str, float]:
    """Per-stage voice latency, from the client's hotkey to the first visible result."""
    if send.voice is None:
        return {}
    out: dict[str, float] = {}
    if send.voice.hotkey_to_first_partial_ms is not None:
        out["hotkey_to_first_partial_ms"] = round(send.voice.hotkey_to_first_partial_ms, 1)
    if send.voice.release_to_final_ms is not None:
        out["release_to_final_ms"] = round(send.voice.release_to_final_ms, 1)
    if turn.first_token_ms is not None:
        key = "final_to_first_token_ms" if route == "model" else "final_to_first_action_ms"
        out[key] = round(turn.first_token_ms, 1)
    if turn.speculation:
        out["speculation_used"] = 1.0 if turn.speculation == "used" else 0.0
    return out
