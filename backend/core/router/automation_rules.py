"""Instant (no-LLM) phrases that create routines and watchers."""

import re

from core.router.rules import Route
from core.schedule import local_now, parse_once, parse_recurring

_LEAD_NOISE = re.compile(r"^(?:,\s*|to\s+|please\s+)+", re.I)
_REMIND = re.compile(r"^remind me (?:to |that |about )?", re.I)
_ONCE_TAIL = (
    r"(?P<when>at \S+(?: ?[ap]\.?m\.?)?|tomorrow at \S+(?: ?[ap]\.?m\.?)?|in \d+ (?:minutes?|hours?))"
)


def _echo(output: str, ok: bool) -> str:
    return output if ok else f"I couldn't set that up. {output}"


def _routine(prompt: str, when: str, action: str) -> Route:
    args = {"prompt": prompt.strip(" ,."), "when": when.strip(" ,"), "action": action}
    return Route(rule="routine", tool="create_routine", args=args, verify=True, render=_echo)


def recurring(text: str) -> Route | None:
    """'every weekday at 8am, check the build' / 'remind me every monday at 9 to pay rent'."""
    body, action = text, "run"
    if m := re.match(r"^remind me (?P<rest>every .+)$", text, re.I):
        body, action = m["rest"], "notify"
    if not re.match(r"^(every|daily)\b", body, re.I):
        return None
    words = body.split()
    for cut in range(min(len(words) - 1, 10), 1, -1):  # longest schedule phrase first
        when = " ".join(words[:cut]).rstrip(",")
        if parse_recurring(when) is None:
            continue
        prompt = _LEAD_NOISE.sub("", " ".join(words[cut:])).strip()
        if _REMIND.match(prompt):
            prompt, action = _REMIND.sub("", prompt), "notify"
        return _routine(prompt, when, action) if prompt else None
    return None


def reminder(text: str) -> Route | None:
    """'remind me to call mom at 5pm' / 'remind me in 20 minutes to stretch'."""
    patterns = [
        rf"^remind me (?:to |that |about )?(?P<what>.+?) {_ONCE_TAIL}$",
        rf"^remind me {_ONCE_TAIL},? (?:to |that |about )?(?P<what>.+)$",
        rf"^{_ONCE_TAIL},? remind me (?:to |that |about )?(?P<what>.+)$",
    ]
    for pattern in patterns:
        m = re.match(pattern, text, re.I)
        if m and parse_once(m["when"], local_now()) is not None:
            return _routine(m["what"], m["when"], "notify")
    return None


_WATCH_LEAD = re.compile(r"^(?:tell me|notify me|alert me|let me know|ping me) (?:when|once|if) ", re.I)
_DOMAIN = r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?"


def _watch(kind: str, target: str, condition: str, text: str = "") -> Route:
    args = {"kind": kind, "target": target, "condition": condition, "text": text}
    return Route(rule="watcher", tool="create_watcher", args=args, verify=True, render=_echo)


def watcher(text: str) -> Route | None:
    m = _WATCH_LEAD.match(text)
    if m is None:
        return None
    body = text[m.end() :].strip()
    if w := re.fullmatch(rf"(?P<t>{_DOMAIN}) is (?:back )?(?P<c>up|online|down|offline)", body, re.I):
        return _watch("url", w["t"], "up" if w["c"].lower() in ("up", "online") else "down")
    if w := re.fullmatch(rf"(?P<t>{_DOMAIN}) (?:contains|mentions|says|shows) (?P<x>.+)", body, re.I):
        return _watch("url", w["t"], "contains", w["x"].strip("\"'“”"))
    if w := re.fullmatch(
        r"(?:a |the )?window (?:called|named|titled) (?P<t>.+?) "
        r"(?P<c>appears|opens|shows up|closes|disappears|is closed)",
        body,
        re.I,
    ):
        return _watch(
            "window",
            w["t"].strip("\"'“”"),
            "exists" if w["c"] in ("appears", "opens", "shows up") else "gone",
        )
    file_conditions = {
        "changes": "changes", "is changed": "changes", "is modified": "changes", "is updated": "changes",
        "appears": "exists", "exists": "exists", "is created": "exists",
        "is deleted": "gone", "disappears": "gone", "is removed": "gone",
    }  # fmt: skip
    conds = "|".join(re.escape(c) for c in sorted(file_conditions, key=len, reverse=True))
    if w := re.fullmatch(rf"(?P<t>\S+) (?P<c>{conds})", body, re.I):
        target = w["t"]
        if target.startswith(("~", "/", "./")) or ("/" in target and "://" not in target):
            return _watch("file", target, file_conditions[w["c"].lower()])
        if re.fullmatch(_DOMAIN, target, re.I) and file_conditions[w["c"].lower()] == "changes":
            return _watch("url", target, "changes")
    return None


def automation(text: str) -> Route | None:
    return recurring(text) or reminder(text) or watcher(text)
