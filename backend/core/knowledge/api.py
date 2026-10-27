"""HTTP API for the knowledge graph: see everything that was learned, correct it, delete it."""

import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from core.api import _rt
from core.knowledge.retrieve import names_for, phrase, relation_to_user
from core.knowledge.schema import SELF_ID, Entity, Fact
from core.knowledge.store import KnowledgeStore

router = APIRouter(prefix="/api/knowledge")
MAX_ENTITIES = 200


class FactOut(BaseModel):
    id: str
    text: str
    """The fact as a phrase: "prefers contact via: WhatsApp"."""
    predicate: str
    object_id: str | None
    """The other person or place, when the fact links two (it draws a line in the graph)."""
    since: float
    quote: str
    """The user's own words it was learned from."""
    current: bool


class EntityOut(BaseModel):
    id: str
    name: str
    type: str
    aliases: list[str]
    relation: str
    mentions: int
    last_seen_at: float
    facts: list[FactOut]


class KnowledgeOut(BaseModel):
    you: list[FactOut]
    entities: list[EntityOut]
    counts: dict[str, int]


class RenameIn(BaseModel):
    name: str


class MergeIn(BaseModel):
    keep: str
    drop: str


def _store(request: Request) -> KnowledgeStore:
    return _rt(request).knowledge.store


async def _facts(store: KnowledgeStore, entity_id: str, history: bool) -> list[FactOut]:
    facts = await store.facts(entity_id, current=not history)
    names = await names_for(store, facts)
    return [_out(f, names) for f in facts]


def _out(fact: Fact, names: dict[str, str]) -> FactOut:
    return FactOut(
        id=fact.id,
        text=phrase(fact, names),
        predicate=fact.predicate,
        object_id=fact.object_id,
        since=fact.recorded_at,
        quote=fact.quote,
        current=fact.superseded_at is None,
    )


async def _entity(store: KnowledgeStore, entity: Entity, history: bool) -> EntityOut:
    return EntityOut(
        id=entity.id,
        name=entity.name,
        type=entity.type,
        aliases=[a for a in entity.aliases if a.casefold() != entity.name.casefold()],
        relation=await relation_to_user(store, entity.id),
        mentions=entity.mentions,
        last_seen_at=entity.last_seen_at,
        facts=await _facts(store, entity.id, history),
    )


@router.get("")
async def knowledge(
    request: Request, q: str = Query(default="", max_length=100), history: bool = False
) -> KnowledgeOut:
    store = _store(request)
    await store.ensure_self()
    found = await store.search(q, MAX_ENTITIES)
    return KnowledgeOut(
        you=await _facts(store, SELF_ID, history),
        entities=[await _entity(store, e, history) for e in found],
        counts=await store.counts(),
    )


@router.get("/recent")
async def recent(request: Request, hours: int = Query(default=24, ge=1, le=24 * 30)) -> list[FactOut]:
    store = _store(request)
    facts = await store.recent(time.time() - hours * 3600)
    names = await names_for(store, facts)
    return [
        _out(f, names).model_copy(update={"text": f"{names.get(f.subject_id, '?')}: {phrase(f, names)}"})
        for f in facts
    ]


@router.delete("/facts/{fact_id}", status_code=204)
async def delete_fact(request: Request, fact_id: str) -> None:
    rt = _rt(request)
    if not await rt.knowledge.store.delete_fact(fact_id):
        raise HTTPException(404, "no such fact")
    await rt.store.audit("user", "knowledge.forget_fact", {"fact_id": fact_id})


@router.delete("/entities/{entity_id}", status_code=204)
async def delete_entity(request: Request, entity_id: str) -> None:
    rt = _rt(request)
    if not await rt.knowledge.store.delete_entity(entity_id):
        raise HTTPException(404, "no such entry")
    await rt.store.audit("user", "knowledge.forget_entity", {"entity_id": entity_id})


@router.put("/entities/{entity_id}", status_code=204)
async def rename(request: Request, entity_id: str, body: RenameIn) -> None:
    rt = _rt(request)
    if not await rt.knowledge.store.rename(entity_id, body.name):
        raise HTTPException(400, "can't rename that")
    await rt.store.audit("user", "knowledge.rename", {"entity_id": entity_id})


@router.post("/merge", status_code=204)
async def merge(request: Request, body: MergeIn) -> None:
    rt = _rt(request)
    if not await rt.knowledge.store.merge(body.keep, body.drop):
        raise HTTPException(400, "can't merge those two")
    await rt.store.audit("user", "knowledge.merge", {"keep": body.keep, "drop": body.drop})
