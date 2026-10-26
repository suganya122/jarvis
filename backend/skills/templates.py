"""Slot placeholders: `{name}` inserts the value, `{name|url}` inserts it URL-encoded."""

import re
from typing import Any
from urllib.parse import quote_plus

SLOT = re.compile(r"\{([a-z][a-z0-9_]*)(\|url)?\}")


def slots_in(template: Any) -> set[str]:
    if isinstance(template, str):
        return {m.group(1) for m in SLOT.finditer(template)}
    if isinstance(template, dict):
        return set().union(*(slots_in(v) for v in template.values())) if template else set()
    if isinstance(template, list):
        return set().union(*(slots_in(v) for v in template)) if template else set()
    return set()


def fill(template: Any, values: dict[str, str], numbers: frozenset[str] = frozenset()) -> Any:
    """Fill placeholders. A string that is exactly one number slot becomes a number."""
    if isinstance(template, dict):
        return {k: fill(v, values, numbers) for k, v in template.items()}
    if isinstance(template, list):
        return [fill(v, values, numbers) for v in template]
    if not isinstance(template, str):
        return template
    whole = SLOT.fullmatch(template)
    if whole and whole.group(1) in numbers:
        raw = values[whole.group(1)]
        return float(raw) if "." in raw else int(raw)

    def replace(m: re.Match[str]) -> str:
        value = values[m.group(1)]
        return quote_plus(value) if m.group(2) else value

    return SLOT.sub(replace, template)
