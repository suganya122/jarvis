"""Who or what a word refers to: names (exact, then by sound, since speech recognition mishears
them), the user's own ways of saying it ("my brother"), and, when learning, whether a new name is
someone already known."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from core.knowledge.schema import LABELS, SELF_ID, Entity, norm
from core.knowledge.store import KnowledgeStore

SOUNDS_LIKE = 0.8
MIN_SKELETON = 4
MAX_FUZZY = 3
SELF_WORDS = frozenset({"me", "myself", "i", "you", "user", "the user", "self"})
GENERIC_LABELS = frozenset(
    {"friend", "buddy", "mate", "partner", "colleague", "coworker", "teammate", "manager", "child", "kid"}
    | {"parent", "sibling", "spouse"}
)
"""Words that name a relation only after "my": "add a friend" is not about a person in the graph."""
STOP = frozenset(
    "the and for with from that this what when where who how can could would should please about my "
    "your our send call message open play show tell give make take find get set put any some more".split()
)


@dataclass(frozen=True)
class Match:
    entity_id: str
    how: str
    """"name", "relation" (my brother), or "sounds like" (heard "Sreenath")."""
    heard: str = ""


def skeleton(word: str) -> str:
    """Consonants only (after the first letter): how alike two spellings of a name sound.
    "sreenath" and "srinith" both give "srnth"."""
    return word[:1] + re.sub(r"[aeiou]+", "", word[1:])


def likeness(heard: str, name: str) -> float:
    """0-1: how much a heard word could be this name. Speech recognition keeps a name's consonants
    and shuffles its vowels, so alike consonants (and a similar spelling) count for a lot."""
    ratio = SequenceMatcher(None, heard, name).ratio()
    a, b = skeleton(heard), skeleton(name)
    if (
        len(a) >= MIN_SKELETON
        and heard[0] == name[0]
        and ratio >= 0.55
        and SequenceMatcher(None, a, b).ratio() >= 0.85
    ):
        return max(ratio, 0.9)
    return ratio


def grams(words: list[str], longest: int = 3) -> list[str]:
    return list(
        dict.fromkeys(
            " ".join(words[i : i + n]) for n in range(1, longest + 1) for i in range(len(words) - n + 1)
        )
    )


async def find(store: KnowledgeStore, request: str) -> list[Match]:
    """Everything in the graph the request seems to be about, best evidence first."""
    words = norm(request).split()
    if not words:
        return []
    matches: dict[str, Match] = {}
    named = await store.entity_ids_for_aliases([g for g in grams(words) if g not in STOP])
    for gram, ids in named.items():
        for entity_id in ids:
            if entity_id != SELF_ID and gram not in SELF_WORDS:
                matches.setdefault(entity_id, Match(entity_id, "name", gram))
    for word in words:  # part of a longer name: "srinith" for "Srinith Kumar"
        if len(word) >= 3 and word not in STOP and word not in SELF_WORDS and word not in named:
            for entity_id in await store.entities_with_word(word):
                matches.setdefault(entity_id, Match(entity_id, "name", word))
    for index, word in enumerate(words):
        if (labels := LABELS.get(word)) is None:
            continue
        if word in GENERIC_LABELS and (index == 0 or words[index - 1] not in ("my", "our")):
            continue
        for predicate, direction in labels:
            for entity_id in await store.related_to_self(predicate, direction):
                matches.setdefault(entity_id, Match(entity_id, "relation", word))
    heard = [w for w in words if len(w) >= 4 and w not in STOP and w not in named]
    if heard and len(matches) < 12:
        aliases = await store.single_word_aliases()
        for word in heard:
            best = max(
                ((likeness(word, alias), alias, entity_id) for alias, entity_id in aliases),
                default=(0.0, "", ""),
            )
            if best[0] >= SOUNDS_LIKE and best[2] != SELF_ID and best[2] not in matches:
                matches[best[2]] = Match(best[2], "sounds like", word)
                if sum(m.how == "sounds like" for m in matches.values()) >= MAX_FUZZY:
                    break
    return list(matches.values())


async def resolve_name(store: KnowledgeStore, name: str, type_: str) -> Entity | None:
    """The entity a name the user just said already is, or None (it's someone new).

    Exact names only, plus one safe shortcut: a first name that is part of exactly one known full
    name ("Srinith" for "Srinith Kumar"). Anything fuzzier is left as a new entry for the user to
    merge: wrongly joining two people is worse than having two entries for one."""
    key = norm(name)
    if not key:
        return None
    if key in SELF_WORDS:
        return await store.entity(SELF_ID)
    exact = await store.entities((await store.entity_ids_for_aliases([key])).get(key, []))
    same_type = [e for e in exact.values() if e.type == type_]
    if same_type:
        return max(same_type, key=lambda e: e.mentions)
    if type_ != "person":
        return None
    tokens = set(key.split())
    partial = [
        e
        for e in await store.search(key.split()[0], limit=10)
        if e.type == "person"
        and any(tokens <= set(norm(a).split()) or set(norm(a).split()) <= tokens for a in e.aliases)
    ]
    return partial[0] if len(partial) == 1 else None
