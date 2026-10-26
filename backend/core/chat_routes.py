"""The no-model routes of a chat turn: screen attachments, skill replays, instant commands.

Split from chat.py to keep files small; ChatService mixes these in.
"""

from collections.abc import Callable

from contracts.model import ChatMessage, Role, ToolCallRequested
from contracts.ws import ChatDelta, ChatSend
from core.chat_finish import FinishMixin
from core.chat_types import Emit
from core.config import Settings
from core.router import Route
from core.skill_runner import SkillRunner
from core.store import Store
from core.tool_exec import ChatPermissions, ToolExecutor
from core.toolbox import ToolRegistry
from core.turn import Turn
from skills.matcher import SkillMatch


class RoutesMixin(FinishMixin):
    _store: Store
    _executor: ToolExecutor
    _registry: ToolRegistry
    _skills: SkillRunner | None
    _clock: Callable[[], float]

    async def _user_message(
        self, send: ChatSend, emit: Emit, turn: Turn, perms: ChatPermissions
    ) -> ChatMessage:
        """The user's text, plus a screenshot when they asked to attach one."""
        if send.attach == "none":
            return ChatMessage(role=Role.USER, text=send.text)
        if self._registry.get("capture_screen") is None:
            return ChatMessage(
                role=Role.USER, text=f"{send.text}\n\n(Screen capture isn't available right now.)"
            )
        call = ToolCallRequested(
            call_id=f"attach_{turn.trace_id[:12]}", name="capture_screen", input={"target": send.attach}
        )
        # The user pressed the button themselves, so this is a direct request.
        outcome = await self._executor.execute(
            call,
            request_id=send.request_id,
            trace_id=turn.trace_id,
            perms=perms,
            emit=emit,
            direct=True,
            background=turn.background,
        )
        capture = outcome.evidence.get("capture")
        if outcome.status != "ok" or not capture:
            note = f"(I tried to attach a screenshot, but: {outcome.block.output})"
            return ChatMessage(role=Role.USER, text=f"{send.text}\n\n{note}")
        note = (
            f"(Attached screenshot {capture['capture_id']}, {capture['width']}x{capture['height']} pixels.)"
        )
        return ChatMessage(role=Role.USER, text=f"{send.text}\n\n{note}", images=outcome.block.images)

    async def _run_skill(
        self,
        send: ChatSend,
        emit: Emit,
        turn: Turn,
        found: SkillMatch,
        perms: ChatPermissions,
        route_ms: float,
        settings: Settings,
    ) -> None:
        """Replay a learned skill: zero LLM calls, every step through the policy gate."""
        assert self._skills is not None and turn.session_id is not None
        started = self._clock()
        result = await self._skills.replay(
            found,
            settings.skills,
            request_id=send.request_id,
            trace_id=turn.trace_id,
            perms=perms,
            emit=emit,
            background=turn.background,
        )
        timings = {"route_ms": round(route_ms, 2), "action_ms": round((self._clock() - started) * 1000, 2)}
        turn.first_token_ms = (self._clock() - turn.started) * 1000
        await emit(ChatDelta(request_id=send.request_id, text=result.reply))
        reply = ChatMessage(role=Role.ASSISTANT, text=result.reply)
        await self._store.append_message(turn.session_id, turn.trace_id, reply)
        turn.model = "skill"
        stop = "end_turn" if result.ok else "skill_failed"
        await self._done(
            send,
            emit,
            turn,
            stop,
            route="skill",
            timings=timings,
            rule=found.skill.skill_id,
            skill=result.note,
        )

    async def _instant(
        self,
        send: ChatSend,
        emit: Emit,
        turn: Turn,
        route: Route,
        perms: ChatPermissions,
        route_ms: float,
        direct: bool,
    ) -> None:
        """Answer from the rule router: zero LLM calls, same policy gate and audit trail."""
        assert turn.session_id is not None
        timings = {"route_ms": round(route_ms, 2)}
        if route.answer is not None:
            reply = route.answer
        else:
            assert route.tool is not None
            call = ToolCallRequested(
                call_id=f"instant_{turn.trace_id[:12]}", name=route.tool, input=route.args or {}
            )
            action_started = self._clock()
            outcome = await self._executor.execute(
                call,
                request_id=send.request_id,
                trace_id=turn.trace_id,
                perms=perms,
                emit=emit,
                direct=direct,
                background=turn.background,
            )
            timings["action_ms"] = round((self._clock() - action_started) * 1000, 2)
            ok = outcome.status == "ok"
            reply = route.render(outcome.block.output, ok) if route.render else outcome.block.output
        turn.first_token_ms = (self._clock() - turn.started) * 1000
        await emit(ChatDelta(request_id=send.request_id, text=reply))
        await self._store.append_message(
            turn.session_id, turn.trace_id, ChatMessage(role=Role.ASSISTANT, text=reply)
        )
        turn.model = "instant"
        await self._done(send, emit, turn, "end_turn", route="instant", timings=timings, rule=route.rule)
