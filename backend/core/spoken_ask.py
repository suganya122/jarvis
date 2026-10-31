"""How a step that needs the user's OK is asked aloud: a few words, quick to hear and answer (the
card shows the rest)."""

import re

from contracts.common import MONEY
from contracts.ws import ApprovalRequested

SPOKEN_ASK_CHARS = 80
SERIOUS = {"external_send": "This sends something", "purchase": "This spends money"}


def approval_question(msg: ApprovalRequested) -> str:
    """A few words, quick to hear and answer: what's about to happen (the card shows the rest),
    and the kind of step when it matters."""
    what = brief(CODE.sub(_where, msg.summary))
    kind = SERIOUS.get(str(msg.risk))
    return f"{kind}: {what}. Go ahead?" if kind else f"{what}. Go ahead?"


CODE = re.compile(r":?\s*`([^`]*)`")
ABBREVIATION = re.compile(r"(?:^|\s)\S{1,3}$")
"""'T.' in 'T. Nagar', 'Rs.', 'St.': a full stop after these doesn't end the sentence."""


def _where(code: re.Match[str]) -> str:
    """A command isn't read out: the folders and sites it touches are ('in Downloads'). A few plain
    words are ('killall Spotify')."""
    if len(code.group(1)) <= 30 and not re.search(r"[~/|&;<>$\\-]", code.group(1)):
        return f": {code.group(1)}"
    sites = re.findall(r"https?://([\w.-]+)", code.group(1))
    paths = re.findall(r"(?<![\w:/.])~?/[\w.~/-]*\w", code.group(1))
    names = list(dict.fromkeys(p.rstrip("/").rsplit("/", 1)[-1] for p in paths))
    return (f" in {', '.join(names)}" if names else "") + (f" to {', '.join(sites)}" if sites else "")


def brief(summary: str, limit: int = SPOKEN_ASK_CHARS) -> str:
    """The summary's first line, ended at a clause when it's long; an amount of money stays in."""
    text = summary.split("\n", 1)[0].strip().rstrip(".")
    if len(text) <= limit:
        return text
    cut, said = text[:limit], text[:limit].rsplit(" ", 1)[0]
    for mark in (". ", ": ", "; ", " (", ", "):
        end = cut.rfind(mark)
        while mark == ". " and end >= 0 and ABBREVIATION.search(cut[:end]):
            end = cut.rfind(mark, 0, end)
        if end >= limit // 3:
            said = cut[:end]
            break
    money = MONEY.search(text)
    return f"{said}, {money.group()}" if money and not MONEY.search(said) else said
