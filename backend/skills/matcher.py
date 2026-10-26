"""Embedding-free skill matching: each trigger becomes an anchored pattern with slots."""

import re
from dataclasses import dataclass

from contracts.skills import Skill
from skills.templates import SLOT

MAX_SLOT_CHARS = 200
NUMBER = r"-?\d+(?:\.\d+)?"


@dataclass(frozen=True)
class SkillMatch:
    skill: Skill
    values: dict[str, str]
    literal_chars: int
    """How much of the trigger was fixed text; more means more specific."""


def trigger_pattern(trigger: str, numbers: frozenset[str]) -> re.Pattern[str]:
    parts: list[str] = []
    last = 0
    seen: set[str] = set()
    for m in SLOT.finditer(trigger):
        parts.append(r"\s+".join(re.escape(w) for w in trigger[last : m.start()].split(" ")))
        name = m.group(1)
        if name in seen:  # a slot used twice must match the same text
            parts.append(f"(?P={name})")
        else:
            parts.append(f"(?P<{name}>{NUMBER if name in numbers else '.+?'})")
            seen.add(name)
        last = m.end()
    parts.append(r"\s+".join(re.escape(w) for w in trigger[last:].split(" ")))
    return re.compile("".join(parts), re.IGNORECASE)


def literal_chars(trigger: str) -> int:
    return len(re.sub(r"\s", "", SLOT.sub("", trigger)))


def _clean_value(value: str) -> str | None:
    value = value.strip().strip("\"'\u201c\u201d\u2018\u2019")
    if not value or len(value) > MAX_SLOT_CHARS or "\n" in value:
        return None
    return value


def match(text: str, skills: list[Skill]) -> SkillMatch | None:
    """The single most specific active skill whose trigger fully matches, else None."""
    found: list[SkillMatch] = []
    for skill in skills:
        if skill.status != "active" or not skill.enabled:
            continue
        numbers = frozenset(p.name for p in skill.params if p.kind == "number")
        for trigger in skill.triggers:
            m = trigger_pattern(trigger, numbers).fullmatch(text)
            if m is None:
                continue
            values = {k: _clean_value(v) for k, v in m.groupdict().items()}
            if any(v is None for v in values.values()):
                continue
            found.append(SkillMatch(skill, {k: v for k, v in values.items() if v}, literal_chars(trigger)))
            break
    if not found:
        return None
    found.sort(key=lambda f: -f.literal_chars)
    if (
        len(found) > 1
        and found[0].literal_chars == found[1].literal_chars
        and found[0].skill != found[1].skill
    ):
        return None  # ambiguous: let the model decide
    return found[0]
