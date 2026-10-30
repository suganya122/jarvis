"""What the knowledge graph can hold: kinds of things, a closed vocabulary of facts, and how people
name the relations in their life ("my brother").

The vocabulary is closed on purpose: the model that learns facts must pick from this list, so a
sentence it was merely shown can't become a new kind of fact, and "lives in" always means the same."""

import re
from dataclasses import dataclass

SELF_ID = "self"
"""The person using the assistant. "I", "me", "my brother" all hang off this one entity."""
ENTITY_TYPES = ("person", "place", "org", "app", "project", "thing")

# predicate -> (cardinality, object). "one": a new value replaces the old (they moved); "many": adds.
# object "entity": another thing in the graph; "text": a short phrase.
PREDICATES: dict[str, tuple[str, str]] = {
    "lives_in": ("one", "entity"),
    "works_at": ("one", "entity"),
    "partner_of": ("one", "entity"),
    "sibling_of": ("many", "entity"),
    "parent_of": ("many", "entity"),
    "child_of": ("many", "entity"),
    "friend_of": ("many", "entity"),
    "colleague_of": ("many", "entity"),
    "manager_of": ("many", "entity"),
    "member_of": ("many", "entity"),
    "uses": ("many", "entity"),
    "works_on": ("many", "entity"),
    "studies_at": ("one", "entity"),
    "comes_from": ("one", "entity"),
    "job_title": ("one", "text"),
    "birthday": ("one", "text"),
    "prefers_contact_via": ("one", "text"),
    "speaks": ("many", "text"),
    "likes": ("many", "text"),
    "dislikes": ("many", "text"),
    "prefers": ("many", "text"),
    "routine": ("many", "text"),
    "goal": ("many", "text"),
    "decided": ("many", "text"),
    "wants_assistant_to": ("many", "text"),
    "note": ("many", "text"),
}
INSTRUCTION = "wants_assistant_to"
"""How the user wants the assistant to work ("open links in my own browser"), from their own words.
Unlike the other facts, these are followed: they are the user's standing instructions."""
SYMMETRIC = frozenset({"sibling_of", "friend_of", "colleague_of", "partner_of"})
"""A sibling_of B means B sibling_of A: either direction finds it."""

RELATION_WORDS: dict[str, str] = {
    "sibling_of": "sibling",
    "friend_of": "friend",
    "colleague_of": "colleague",
    "partner_of": "partner",
    "manager_of": "manager",
}

# What people call each other: word -> ((predicate, direction), ...). "in": the person is the
# subject and the user the object (X parent_of me); "out": the other way; "any": either.
LABELS: dict[str, tuple[tuple[str, str], ...]] = {
    **dict.fromkeys(("brother", "sister", "sibling"), (("sibling_of", "any"),)),
    **dict.fromkeys(("friend", "buddy", "mate"), (("friend_of", "any"),)),
    **dict.fromkeys(("colleague", "coworker", "teammate"), (("colleague_of", "any"),)),
    **dict.fromkeys(
        ("wife", "husband", "partner", "spouse", "girlfriend", "boyfriend", "fiance"),
        (("partner_of", "any"),),
    ),
    **dict.fromkeys(
        ("mom", "mother", "mum", "dad", "father", "parent"), (("parent_of", "in"), ("child_of", "out"))
    ),
    **dict.fromkeys(("son", "daughter", "child", "kid"), (("parent_of", "out"), ("child_of", "in"))),
    **dict.fromkeys(("boss", "manager"), (("manager_of", "in"),)),
}


@dataclass(slots=True)
class Entity:
    id: str
    type: str
    name: str
    created_at: float
    last_seen_at: float
    mentions: int = 1
    aliases: tuple[str, ...] = ()


@dataclass(slots=True)
class Fact:
    id: str
    subject_id: str
    predicate: str
    object_id: str | None
    object_text: str | None
    valid_from: float
    recorded_at: float
    superseded_at: float | None
    confidence: float
    origin: str
    quote: str
    hits: int = 0
    last_used_at: float | None = None


def norm(text: str) -> str:
    """Comparable form of a name: lower case, letters and digits, single spaces."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())
