"""Skills in the request pipeline: match -> replay (no LLM) -> verify; learn after model turns."""

import logging
import time
import uuid

from contracts.model import ToolCallRequested
from contracts.skills import Skill, SkillNote, TraceStep
from core.chat_types import Emit
from core.config import SkillSettings
from core.store import Store
from core.tool_exec import ChatPermissions, ToolExecutor
from skills.learner import Draft, NotLearnable, learn
from skills.matcher import SkillMatch, match
from skills.store import SkillStore
from skills.templates import fill

log = logging.getLogger(__name__)
MAX_TRIGGERS = 8


class ReplayResult:
    def __init__(self, ok: bool, reply: str, note: SkillNote, steps_run: int) -> None:
        self.ok = ok
        self.reply = reply
        self.note = note
        self.steps_run = steps_run


def _note(event: str, skill: Skill, needed: int) -> SkillNote:
    data = {"event": event, "skill_id": skill.skill_id, "name": skill.name,
            "successes": skill.verified_successes, "needed": needed}  # fmt: skip
    return SkillNote.model_validate(data)


class SkillRunner:
    def __init__(self, skills: SkillStore, executor: ToolExecutor, store: Store) -> None:
        self.skills = skills
        self._executor = executor
        self._store = store

    async def find(self, cleaned_text: str) -> SkillMatch | None:
        return match(cleaned_text, await self.skills.all())

    async def replay(
        self,
        found: SkillMatch,
        settings: SkillSettings,
        *,
        request_id: str,
        trace_id: str,
        perms: ChatPermissions,
        emit: Emit,
        background: bool = False,
    ) -> ReplayResult:
        skill = found.skill
        numbers = frozenset(p.name for p in skill.params if p.kind == "number")
        await self._store.audit(
            "system", "skill.replay", {"skill": skill.skill_id, "values": found.values}, trace_id
        )
        for index, step in enumerate(skill.steps):
            call = ToolCallRequested(
                call_id=f"skill_{trace_id[:8]}_{index}",
                name=step.tool,
                input=fill(step.args_template, found.values, numbers),
            )
            outcome = await self._executor.execute(
                call,
                request_id=request_id,
                trace_id=trace_id,
                perms=perms,
                emit=emit,
                direct=settings.replay_without_asking,
                background=background,
            )
            verified = outcome.evidence.get("verified") is True
            if outcome.status != "ok" or ("verified" in step.checks and not verified):
                why = outcome.block.output if outcome.status != "ok" else "its effect couldn't be confirmed"
                failed = await self._record_failure(skill, settings)
                reply = (
                    f"The saved skill “{skill.name}” stopped at step {index + 1} of {len(skill.steps)}: {why}"
                )
                return ReplayResult(False, reply, failed, index)
        updated = skill.model_copy(
            update={
                "verified_successes": skill.verified_successes + 1,
                "consecutive_failures": 0,
                "last_used_at": time.time(),
            }
        )
        await self.skills.save(updated)
        steps = len(skill.steps)
        reply = f"Done. Ran your saved skill “{skill.name}” ({steps} step{'s' if steps != 1 else ''})."
        return ReplayResult(True, reply, _note("replayed", updated, settings.promote_after), steps)

    async def _record_failure(self, skill: Skill, settings: SkillSettings) -> SkillNote:
        consecutive = skill.consecutive_failures + 1
        demote = consecutive >= settings.demote_after_failures
        updated = skill.model_copy(
            update={
                "failures": skill.failures + 1,
                "consecutive_failures": consecutive,
                "status": "demoted" if demote else skill.status,
                "last_used_at": time.time(),
            }
        )
        await self.skills.save(updated)
        if demote:
            await self._store.audit("system", "skill.demoted", {"skill": skill.skill_id})
        return _note("demoted" if demote else "failed", updated, settings.promote_after)

    async def observe(
        self,
        request: str,
        trace: list[TraceStep],
        tainted_before: bool,
        settings: SkillSettings,
        trace_id: str,
    ) -> SkillNote | None:
        """After a successful model turn: learn a new candidate or count toward an existing skill."""
        if not settings.learn or not trace:
            return None
        try:
            draft = learn(request, trace, tainted_before)
        except NotLearnable as exc:
            log.info("not learned (trace %s): %s", trace_id, exc)
            return None
        return await self._merge(draft, settings, trace_id)

    async def _merge(self, draft: Draft, settings: SkillSettings, trace_id: str) -> SkillNote | None:
        existing = await self.skills.by_signature(draft.signature)
        now = time.time()
        if existing is None:
            status = "active" if settings.promote_after <= 1 else "candidate"
            skill = Skill(
                skill_id=uuid.uuid4().hex[:12],
                name=draft.trigger,
                triggers=(draft.trigger,),
                params=draft.params,
                steps=draft.steps,
                signature=draft.signature,
                status=status,
                verified_successes=1,
                created_at=now,
            )
            await self.skills.save(skill)
            detail = {"skill": skill.skill_id, "trigger": draft.trigger}
            await self._store.audit("system", "skill.learned", detail, trace_id)
            return _note("learned" if status == "candidate" else "promoted", skill, settings.promote_after)
        triggers = existing.triggers
        if draft.trigger.casefold() not in {t.casefold() for t in triggers} and len(triggers) < MAX_TRIGGERS:
            triggers = (*triggers, draft.trigger)
        successes = existing.verified_successes + 1
        status = existing.status
        event = None
        if status == "demoted":
            status, successes, event = "candidate", 1, "progress"
        if status == "candidate":
            if successes >= settings.promote_after:
                status, event = "active", "promoted"
            else:
                event = "progress"
        updated = existing.model_copy(
            update={
                "triggers": triggers,
                "verified_successes": successes,
                "status": status,
                "consecutive_failures": 0,
            }
        )
        await self.skills.save(updated)
        if event == "promoted":
            await self._store.audit("system", "skill.promoted", {"skill": existing.skill_id}, trace_id)
        return _note(event, updated, settings.promote_after) if event else None
