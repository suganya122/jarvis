"""Turn one verified tool trace into a skill draft, or explain why it can't be learned.

Conservative by design: a skill replays without any model judgement, so anything whose
arguments might depend on what the model read, wrote, or decided is not learned.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, urlparse

from contracts.skills import Check, ParamSlot, SkillStep, TraceStep

# Used only to find things; replaced by locators in the skill.
LOCATE_ONLY = frozenset({"list_windows", "find_elements", "accessibility_status", "browser_read"})
# Browsing steps return the page (untrusted) but act on stable locators; learnable only when
# what they click doesn't carry page-specific content (prices, dates, flight numbers).
BROWSE = frozenset({"browser_open", "browser_click", "browser_type", "browser_select", "browser_back"})
NEVER_LEARN = frozenset({"browser_finish", "browser_handover"})
PAGE_SPECIFIC = re.compile(r"[\d$€£₹¥]")
# Their output can shape later arguments, so a turn that uses them isn't replayable.
CONTENT_READS = frozenset(
    {"read_text_file", "read_element", "capture_screen", "list_directory", "system_info", "current_time",
     "highlight_area", "run_command"}
)  # fmt: skip
ID_ARGS = {"element_id": "element", "window_id": "window", "ref": "element"}
FREE_TEXT_KEYS = frozenset({"content", "text"})
MAX_GENERATED_CHARS = 40
STOPWORDS = frozenset(
    "a an and the to for of on in at with my me please then also open launch start go search find "
    "set make it this that is are be can you could would will".split()
)
SECRET = re.compile(
    r"(sk-[a-z0-9_-]{8,}|bearer\s+\S{8,}|password|passwd|api[_-]?key|token|[a-z0-9+/_-]{32,})", re.I
)
SLOT_NAMES = {
    "name": "app", "app": "app", "url": "query", "path": "path", "query": "query",
    "content": "text", "text": "text", "keys": "keys", "action": "action",
}  # fmt: skip
SENSITIVE_REQUEST = re.compile(
    r"\b(password|passcode|passphrase|pin|otp|2fa|token|api key|secret|credit card|card number|cvv|ssn|"
    r"social security|bank account|iban|login|log in|sign in)\b",
    re.I,
)
MAX_SPAN_WORDS = 8
MIN_LITERAL_CHARS = 4


@dataclass
class Draft:
    trigger: str
    params: tuple[ParamSlot, ...]
    steps: tuple[SkillStep, ...]
    signature: str


class NotLearnable(Exception):
    pass


def _spans(text: str) -> list[str]:
    """Word spans of the request, longest first, skipping stopword-only spans."""
    words = text.split()
    spans = {
        " ".join(words[i:j])
        for i in range(len(words))
        for j in range(i + 1, min(len(words), i + MAX_SPAN_WORDS) + 1)
    }
    useful = [s for s in spans if len(s) >= 3 and not all(w.casefold() in STOPWORDS for w in s.split())]
    return sorted(useful, key=len, reverse=True)


class _Generalizer:
    def __init__(self, request: str) -> None:
        self.request = request
        self.spans = _spans(request)
        self.slots: dict[str, tuple[str, str]] = {}  # span (casefolded) -> (slot name, kind)

    def _slot_for(self, span: str, key: str, kind: str) -> str:
        found = self.slots.get(span.casefold())
        if found:
            return found[0]
        base = SLOT_NAMES.get(key, re.sub(r"[^a-z0-9_]", "", key.lower()) or "value")
        name, n = base, 2
        while name in {s for s, _ in self.slots.values()}:
            name, n = f"{base}{n}", n + 1
        self.slots[span.casefold()] = (name, kind)
        return name

    def value(self, key: str, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            token = str(value)
            if re.search(rf"(?<![\w.]){re.escape(token)}(?![\w.])", self.request):
                return "{" + self._slot_for(token, key, "number") + "}"
            return value
        if isinstance(value, list):
            return [self.value(key, v) for v in value]
        if not isinstance(value, str):
            return value
        if SECRET.search(value):
            raise NotLearnable("an argument looks like a secret")
        templated = value
        host = urlparse(value).hostname or "" if key == "url" else ""
        for span in self.spans:
            if host and span.casefold() in host.casefold() and span.casefold() != host.casefold():
                continue  # part of a domain is a constant (en.wikipedia.org), not a parameter
            for needle, suffix in ((span, ""), (quote_plus(span), "|url")):
                if suffix and needle == span:
                    continue
                # Only whole tokens: 'volume' must not match inside 'volume_up'.
                pattern = rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])"
                if re.search(pattern, value, flags=re.I):
                    name = self._slot_for(span, key, "text")
                    # Inside a URL's path or query a parameter is always URL-encoded on replay,
                    # whether or not this example needed it; a whole host stays as typed.
                    if host and span.casefold() != host.casefold():
                        suffix = "|url"
                    templated = re.sub(pattern, "{" + name + suffix + "}", value, count=1, flags=re.I)
                    break
            if templated is not value:
                break
        leftover = len(re.sub(r"\{[^}]*\}", "", templated))
        if key in FREE_TEXT_KEYS and leftover > MAX_GENERATED_CHARS // 2:
            raise NotLearnable("the model wrote fresh content that a replay would only repeat")
        return templated

    def trigger(self) -> str:
        trigger = self.request
        for span, (name, _) in sorted(self.slots.items(), key=lambda kv: -len(kv[0])):
            trigger, count = re.subn(re.escape(span), "{" + name + "}", trigger, count=1, flags=re.I)
            if count == 0:
                raise NotLearnable("couldn't map a parameter back to the request")
        return trigger


def _checks(step: TraceStep) -> tuple[Check, ...]:
    return ("ok", "verified") if step.evidence.get("verified") is True else ("ok",)


def _step(step: TraceStep, gen: _Generalizer) -> SkillStep:
    args: dict[str, Any] = {}
    target = step.evidence.get("target") or {}
    for key, value in step.args.items():
        if key in ID_ARGS:
            locator = target.get(ID_ARGS[key])
            if not locator:
                raise NotLearnable(f"{step.tool} target can't be found again later")
            args[key] = locator  # stable locator instead of a session id
        else:
            args[key] = gen.value(key, value)
    return SkillStep(tool=step.tool, args_template=args, checks=_checks(step))


def signature(steps: tuple[SkillStep, ...]) -> str:
    shape = [{"tool": s.tool, "args": s.args_template, "checks": list(s.checks)} for s in steps]
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]


def learn(request: str, trace: list[TraceStep], tainted_before: bool) -> Draft:
    if tainted_before:
        raise NotLearnable("the chat already contained outside content")
    if SENSITIVE_REQUEST.search(request):
        raise NotLearnable("skills never store anything about passwords, logins or payment details")
    if not trace:
        raise NotLearnable("no actions were taken")
    if any(t.status != "ok" for t in trace):
        raise NotLearnable("not every step succeeded")
    # Any read of outside content (by name, or any read-risk tool returning untrusted output)
    # could have shaped the later arguments, so a replay would be blind to it.
    if any(t.tool in NEVER_LEARN or t.evidence.get("handover") for t in trace):
        raise NotLearnable("final steps and hand-overs are always the user's")
    content_read = any(
        t.tool in CONTENT_READS
        or (t.risk == "read" and t.trust == "untrusted" and t.tool not in LOCATE_ONLY | BROWSE)
        for t in trace
    )
    if content_read:
        raise NotLearnable("the steps depended on content the model read")
    for t in trace:
        name = ((t.evidence.get("target") or {}).get("element") or {}).get("name", "")
        if t.tool in BROWSE and PAGE_SPECIFIC.search(str(name)):
            raise NotLearnable("a browsing step picked something the page showed (a price, date or number)")
    actions = [t for t in trace if t.tool not in LOCATE_ONLY]
    if not actions:
        raise NotLearnable("nothing was changed")
    if any(t.evidence.get("verified") is False for t in actions):
        raise NotLearnable("an action had no visible effect")
    gen = _Generalizer(request)
    steps = tuple(_step(t, gen) for t in actions)
    trigger = gen.trigger()
    if re.sub(r"\{[^}]*\}|\s", "", trigger).__len__() < MIN_LITERAL_CHARS:
        raise NotLearnable("the request is too generic to recognise again")
    params = tuple(
        ParamSlot(name=name, kind="number" if kind == "number" else "text", example=span)
        for span, (name, kind) in gen.slots.items()
    )
    return Draft(trigger=trigger, params=params, steps=steps, signature=signature(steps))
