"""Deterministic rules for obvious requests. Each rule either fully matches or passes.

Rules are anchored (fullmatch) and refuse compound requests, so anything that needs
judgement falls through to the model.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, urlparse

from core.config import RouterSettings
from core.router.calc import CalcError, evaluate, format_number

Render = Callable[[str, bool], str]


@dataclass(frozen=True)
class Route:
    rule: str
    tool: str | None = None
    args: dict[str, Any] | None = None
    answer: str | None = None
    """Set for pure answers that need no tool (arithmetic)."""
    verify: bool = False
    """Fall through to the model unless the tool's assessment passes (e.g. app exists)."""
    render: Render | None = None


def _say(ok_text: str) -> Render:
    return lambda output, ok: ok_text if ok else f"I couldn't do that. {output}"


def _echo(output: str, ok: bool) -> str:
    return output if ok else f"I couldn't do that. {output}"


_LEAD = re.compile(
    r"^(?:(?:please|pls|hey|ok|okay)[,\s]+)*(?:(?:can|could|would|will) you\s+)?(?:please\s+)?", re.IGNORECASE
)
_COMPOUND = re.compile(r"\b(?:and|then|also|after that|before)\b|[,;]", re.IGNORECASE)
_SEARCH_COMPOUND = re.compile(
    r"\b(?:and|then)\s+(?:book|buy|order|send|email|open|download|save|write|delete|create|make|tell|call|text)\b",
    re.IGNORECASE,
)


def clean(text: str) -> str:
    out = _LEAD.sub("", text.strip())
    out = re.sub(r"[\s.!?]+$", "", out)
    out = re.sub(r"[\s,]+please$", "", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out)


def _full(pattern: str, text: str) -> re.Match[str] | None:
    return re.fullmatch(pattern, text, flags=re.IGNORECASE)


# ---- answers --------------------------------------------------------------------


def calc(text: str) -> Route | None:
    chars = r"[\d\s.,+\-*/x\u00d7\u00f7^()%]"
    m = _full(
        rf"(?:what(?:'s| is)|calculate|calc|compute|how much is)?\s*(?P<expr>{chars}+(?:of{chars}+)?)", text
    )
    if not m or not re.search(r"\d", m["expr"]) or not re.search(r"[+\-*/x\u00d7\u00f7^%]", m["expr"]):
        return None
    if re.fullmatch(r"\s*\d{4}-\d{1,2}-\d{1,2}\s*|\s*\d{3}-\d{4}\s*", m["expr"]):
        return None  # a date or phone number, not a subtraction
    try:
        value = evaluate(m["expr"])
    except CalcError:
        return None
    return Route(rule="calc", answer=f"{m['expr'].strip()} = {format_number(value)}")


_TIME = [
    r"(?:what(?:'s| is) the )?(?:current )?time(?: is it)?(?: now| right now)?",
    r"what time is it(?: now| right now)?",
    r"(?:what(?:'s| is) )?(?:the |today'?s )?date(?: today)?",
    r"what(?:'s| is) today(?:'s date)?",
    r"what day is (?:it|today)",
]


def time_query(text: str) -> Route | None:
    if any(_full(p, text) for p in _TIME):
        return Route(
            rule="time", tool="current_time", args={}, render=lambda out, ok: f"It's {out}." if ok else out
        )
    return None


_SYSTEM = [
    r"(?:how much )?(?:free )?disk space(?: (?:do i have|is left|left|is free))?",
    r"system info(?:rmation)?",
    r"what (?:os|operating system) (?:am i (?:running|on|using)|is this)",
]


def system_query(text: str) -> Route | None:
    if any(_full(p, text) for p in _SYSTEM):
        return Route(rule="system", tool="system_info", args={}, render=_echo)
    return None


# ---- web ------------------------------------------------------------------------


def search(text: str, settings: RouterSettings) -> Route | None:
    sites = "|".join(re.escape(s) for s in settings.site_search)
    m = _full(rf"search (?P<site>{sites}) for (?P<q>.+)", text) if sites else None
    if m is None:
        m = _full(
            rf"(?P<verb>search(?: the web| online)?|web search|google|look up|lookup)(?: for)? (?P<q>.+?)"
            rf"(?: on (?P<site>{sites or '(?!)'}))?",
            text,
        )
    if m is None or _SEARCH_COMPOUND.search(m["q"]):
        return None
    site = (m.groupdict().get("site") or "").lower()
    if (
        not site
        and (m.groupdict().get("verb") or "").lower() == "google"
        and "google" in settings.site_search
    ):
        site = "google"
    template = settings.site_search.get(site) or settings.search_url
    query = m["q"].strip().strip("\"'“”")
    url = template.replace("{query}", quote_plus(query))
    where = site.capitalize() if site else urlparse(url).netloc.removeprefix("www.")
    return Route(
        rule="search", tool="open_url", args={"url": url}, render=_say(f"Searching {where} for “{query}”.")
    )


COMMON_TLDS = frozenset(
    "com org net io dev app ai co uk us ca de fr es it nl se no fi dk pl in jp cn au nz br ru ch at be ie "
    "edu gov info me tv xyz site online tech so ly gg fm".split()
)


def open_url(text: str) -> Route | None:
    m = _full(r"(?:open|go to|visit|browse to|navigate to) (?P<url>\S+)", text)
    if m is None:
        return None
    raw = m["url"]
    url = raw if re.match(r"https?://", raw, re.IGNORECASE) else f"https://{raw}"
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", host, re.IGNORECASE):
        return None
    if not raw.lower().startswith("http") and host.rsplit(".", 1)[-1].lower() not in COMMON_TLDS:
        return None  # "open notes.txt" is a file, not a website
    return Route(rule="url", tool="open_url", args={"url": url}, render=_say(f"Opened {host}."))


# ---- local ----------------------------------------------------------------------

FOLDERS = {
    "downloads": "~/Downloads",
    "documents": "~/Documents",
    "desktop": "~/Desktop",
    "pictures": "~/Pictures",
    "photos": "~/Pictures",
    "music": "~/Music",
    "videos": "~/Videos",
    "movies": "~/Movies",
    "home": "~",
}


def open_folder(text: str) -> Route | None:
    names = "|".join(FOLDERS)
    m = _full(rf"open (?:my |the )?(?P<f>{names})(?: folder| directory)?", text)
    if m is None:
        return None
    name = m["f"].lower()
    return Route(rule="folder", tool="open_path", args={"path": FOLDERS[name]}, verify=True,
                 render=_say(f"Opened your {name} folder."))  # fmt: skip


_NOT_APPS = frozenset(
    "a an file files folder document documents my this that it new tab window page link email message "
    "the last recent".split()
)


def open_app(text: str) -> Route | None:
    m = _full(
        r"(?:open|launch|start) (?:the )?(?P<app>[a-z0-9][\w .+&'-]{0,39}?)(?: app| application)?", text
    )
    if m is None:
        return None
    app = m["app"].strip()
    words = app.lower().split()
    if len(words) > 4 or any(w in _NOT_APPS for w in words) or "." in app:
        return None
    return Route(rule="app", tool="open_app", args={"name": app}, verify=True, render=_echo)


_VOLUME = r"(?:turn )?(?:the )?volume {d}|turn (?:it|the volume|the sound) {d}"
_MEDIA: list[tuple[str, str]] = [
    (r"play|pause|resume|unpause|play pause|play/pause|toggle playback|"
     r"(?:pause|resume) (?:the )?(?:music|song|video|playback)", "play_pause"),
    (r"(?:next|skip)(?: (?:song|track))?|skip this(?: song| track)?", "next"),
    (r"(?:previous|prev|last) (?:song|track)|previous|go back a (?:song|track)", "previous"),
    (r"stop (?:the )?(?:music|playback|song)", "stop"),
    (_VOLUME.format(d="up") + r"|louder|(?:increase|raise) (?:the )?volume", "volume_up"),
    (_VOLUME.format(d="down") + r"|quieter|(?:decrease|lower) (?:the )?volume", "volume_down"),
    (r"mute|unmute|toggle mute|(?:un)?mute (?:the )?(?:sound|audio|volume)", "mute"),
]  # fmt: skip


def media(text: str) -> Route | None:
    for pattern, action in _MEDIA:
        if _full(pattern, text):
            return Route(
                rule="media", tool="media_control", args={"action": action}, verify=True, render=_echo
            )
    return None


def match(text: str, settings: RouterSettings) -> Route | None:
    """First matching rule wins; order goes from most to least specific."""
    cleaned = clean(text)
    if not cleaned or len(cleaned) > 300 or "\n" in cleaned:
        return None
    for answer_rule in (calc, time_query, system_query):
        if route := answer_rule(cleaned):
            return route
    from core.router.automation_rules import automation  # late imports: they build on Route
    from core.router.memory_rules import memory

    if route := automation(cleaned) or memory(cleaned):
        return route
    if route := search(cleaned, settings):
        return route
    if _COMPOUND.search(cleaned):
        return None
    for action_rule in (open_url, open_folder, media, open_app):
        if route := action_rule(cleaned):
            return route
    return None
