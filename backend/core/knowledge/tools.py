"""Tools for the model: look something up in the knowledge graph, and forget something in it.

Forgetting needs the `memory` capability (so, after reading outside content, a question), the same as
saving a note: what lasts is what a planted instruction goes for."""

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contracts.common import Capability, RiskLevel
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from core.knowledge.resolve import STOP, find
from core.knowledge.retrieve import Retriever, names_for, phrase
from core.knowledge.schema import SELF_ID, Entity, Fact, norm
from core.knowledge.store import KnowledgeStore

DAY_S = 86_400.0
FORGET_WORDS = frozenset("forget delete remove erase stop knowing that about the wrong".split())


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecallArgs(_Args):
    query: str = Field(
        default="", max_length=200, description="A person, place or thing; empty for an overview."
    )
    recent: bool = Field(default=False, description="Only what was learned in the last day.")


class ForgetArgs(_Args):
    what: str = Field(
        min_length=2,
        max_length=200,
        description='Who or what to forget, e.g. "Srinith" or "that Srinith lives in Chennai".',
    )


def _parse[M: BaseModel](model: type[M], args: dict[str, Any]) -> M | str:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        return "invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:3])


class RecallKnowledge:
    info = ToolInfo(
        name="recall_knowledge",
        description=(
            "Look up what you know about the user's people, places, preferences and routines (learned from "
            "what they said). Use it when you need a name, a relation, or a preference you weren't given."
        ),
        input_schema=RecallArgs.model_json_schema(),
        risk=RiskLevel.READ,
        capabilities=(),
        source="core",
    )

    def __init__(self, store: KnowledgeStore) -> None:
        self._store = store
        self._retriever = Retriever(store)

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(RecallArgs, args)
        return ToolAssessment(
            risk=RiskLevel.READ, summary="Look through what I know", error=a if isinstance(a, str) else None
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(RecallArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        await self._store.ensure_self()
        if a.recent:
            return ToolResult(ok=True, output=await self._recent())
        if a.query and not await find(self._store, a.query):  # the profile would otherwise always answer
            return ToolResult(ok=True, output="Nothing about that yet.")
        found = await self._retriever.context(a.query or "my family", max_chars=2_000)
        counts = await self._store.counts()
        if not found:
            return ToolResult(
                ok=True, output=f"Nothing about that yet ({counts['entities']} people/places known)."
            )
        return ToolResult(ok=True, output=found)

    async def _recent(self) -> str:
        facts = await self._store.recent(time.time() - DAY_S)
        if not facts:
            return "Nothing new in the last day."
        names = await names_for(self._store, facts)
        return "Learned in the last day:\n" + "\n".join(
            f"- {names.get(f.subject_id, '?').capitalize()}: {phrase(f, names)}" for f in facts
        )


class ForgetKnowledge:
    info = ToolInfo(
        name="forget_knowledge",
        description=(
            "Forget something learned about a person, place or preference: one fact "
            '("that Srinith lives in Chennai") or everything about someone ("Srinith"). '
            "It won't be learned again unless the user says it afresh."
        ),
        input_schema=ForgetArgs.model_json_schema(),
        risk=RiskLevel.WRITE,
        capabilities=(Capability.MEMORY,),
        source="core",
    )

    def __init__(self, store: KnowledgeStore) -> None:
        self._store = store

    async def _target(self, what: str) -> tuple[Entity | None, Fact | None, str, str]:
        """(entity, fact, summary, error): a fact if the words name one, else the whole entity."""
        matches = await find(self._store, what)
        entities = [e for m in matches if (e := await self._store.entity(m.entity_id))]
        if not entities:
            return None, None, "", f"I don't know anything about “{what}”."
        if len(entities) > 1:
            listed = ", ".join(e.name for e in entities[:5])
            return None, None, "", f"That could be several: {listed}. Which one?"
        entity = entities[0]
        taken = set(norm(entity.name).split()) | {a for alias in entity.aliases for a in norm(alias).split()}
        rest = [w for w in norm(what).split() if w not in taken | FORGET_WORDS | STOP and len(w) > 2]
        if not rest:
            return entity, None, f"Forget everything about {entity.name}", ""
        facts = await self._store.facts(entity.id)
        names = await names_for(self._store, facts)
        scored = sorted(
            ((sum(w in norm(phrase(f, names)) for w in rest), f) for f in facts),
            key=lambda p: p[0],
            reverse=True,
        )
        if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
            return None, None, "", f"I'm not sure which fact about {entity.name} you mean. Say it more fully."
        fact = scored[0][1]
        return entity, fact, f"Forget: {entity.name} {phrase(fact, names)}", ""

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        a = _parse(ForgetArgs, args)
        if isinstance(a, str):
            return ToolAssessment(risk=RiskLevel.WRITE, summary="Forget something", error=a)
        _, _, summary, error = await self._target(a.what)
        return ToolAssessment(
            risk=RiskLevel.WRITE, summary=summary or "Forget something", error=error or None
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = _parse(ForgetArgs, args)
        if isinstance(a, str):
            return ToolResult(ok=False, output=a)
        entity, fact, summary, error = await self._target(a.what)
        if error or entity is None:
            return ToolResult(ok=False, output=error or "Nothing to forget.")
        if entity.id == SELF_ID and fact is None:
            return ToolResult(ok=False, output="Say which fact about you to forget.")
        done = await self._store.delete_fact(fact.id) if fact else await self._store.delete_entity(entity.id)
        return ToolResult(
            ok=done, output=(summary.replace("Forget", "Forgotten", 1) if done else "Already gone.")
        )
