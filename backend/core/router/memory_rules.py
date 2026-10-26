"""Instant (no-LLM) phrases for memory: 'remember …', 'forget …', 'what do you remember'."""

import re

from core.router.rules import Route

_REMEMBER = re.compile(r"^(?:please )?(?:remember|don'?t forget|note) (?:that )?(?P<fact>.+)$", re.I)
_NOT_A_FACT = re.compile(r"^(?:to |when |what |where |who |how |if |me |this|it\b)", re.I)
_FORGET = re.compile(r"^(?:please )?forget (?:that |about |what i said about )?(?P<what>.+)$", re.I)
_RECALL = re.compile(
    r"^what (?:do you|did i ask you to) (?:remember|know)(?: about (?P<about>.+?))?\??$", re.I
)


def _echo(output: str, ok: bool) -> str:
    return output if ok else f"I couldn't do that. {output}"


def memory(text: str) -> Route | None:
    if m := _RECALL.match(text):
        about = (m["about"] or "").strip()
        query = "" if about.lower() in ("", "me", "myself") else about
        return Route(rule="recall", tool="recall_memories", args={"query": query}, render=_echo)
    if (m := _REMEMBER.match(text)) and not _NOT_A_FACT.match(m["fact"]) and len(m["fact"]) > 3:
        return Route(rule="remember", tool="remember", args={"text": m["fact"]}, verify=True, render=_echo)
    if (m := _FORGET.match(text)) and m["what"].lower() not in ("it", "that", "everything"):
        return Route(rule="forget", tool="forget", args={"query": m["what"]}, verify=True, render=_echo)
    return None
