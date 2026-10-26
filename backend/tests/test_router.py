"""Rule router: what must route instantly, and what must fall through to the model."""

import time
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from core.config import RouterSettings
from core.latency import ROUTER_MATCH_BUDGET_MS
from core.router.calc import CalcError, evaluate, format_number
from core.router.rules import clean, match

S = RouterSettings()


@pytest.mark.parametrize(
    ("text", "rule", "tool", "args"),
    [
        ("open firefox", "app", "open_app", {"name": "firefox"}),
        ("Please launch Visual Studio Code", "app", "open_app", {"name": "Visual Studio Code"}),
        ("can you open spotify?", "app", "open_app", {"name": "spotify"}),
        ("start the calculator app", "app", "open_app", {"name": "calculator"}),
        ("open my downloads folder", "folder", "open_path", {"path": "~/Downloads"}),
        ("open photos", "folder", "open_path", {"path": "~/Pictures"}),
        ("open github.com", "url", "open_url", {"url": "https://github.com"}),
        ("go to https://example.org/a?b=1", "url", "open_url", {"url": "https://example.org/a?b=1"}),
        ("what time is it?", "time", "current_time", {}),
        ("what's the date today", "time", "current_time", {}),
        ("how much disk space do I have", "system", "system_info", {}),
        ("next song", "media", "media_control", {"action": "next"}),
        ("pause", "media", "media_control", {"action": "play_pause"}),
        ("turn the volume up", "media", "media_control", {"action": "volume_up"}),
        ("quieter", "media", "media_control", {"action": "volume_down"}),
        ("mute", "media", "media_control", {"action": "mute"}),
    ],
)
def test_routes(text: str, rule: str, tool: str, args: dict[str, str]) -> None:
    route = match(text, S)
    assert route is not None, text
    assert (route.rule, route.tool, route.args) == (rule, tool, args)


@pytest.mark.parametrize(
    ("text", "host", "query"),
    [
        ("search for best pizza near me", "duckduckgo.com", "best pizza near me"),
        ("google salt and pepper shakers", "www.google.com", "salt and pepper shakers"),
        ("look up the speed of light", "duckduckgo.com", "the speed of light"),
        ("search youtube for lofi beats", "www.youtube.com", "lofi beats"),
        ("search cats on wikipedia", "en.wikipedia.org", "cats"),
    ],
)
def test_web_search(text: str, host: str, query: str) -> None:
    route = match(text, S)
    assert route is not None and route.tool == "open_url"
    url = urlparse(route.args["url"])  # type: ignore[index]
    assert url.netloc == host
    assert query in [v[0] for v in parse_qs(url.query).values()]


@pytest.mark.parametrize(
    "text",
    [
        "open firefox and search for cats",  # compound
        "open the file I was working on yesterday",
        "open notes.txt",  # a file, not a site
        "open my last email",
        "launch firefox, then open github",
        "search for flights and book the cheapest one",
        "play some jazz",  # needs judgement: which app, which music
        "what's the weather like?",
        "write me a poem about the time",
        "find report.pdf",
        "2024",
        "2024-10-24",
        "555-1234",
        "",
        "open a new tab",
        "open " + "x" * 80,
    ],
)
def test_falls_through_to_the_model(text: str) -> None:
    assert match(text, S) is None, text


def test_clean_strips_politeness_and_punctuation() -> None:
    assert clean("  Hey, could you please open Firefox please?! ") == "open Firefox"


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("2+2", "2+2 = 4"),
        ("what is 15% of 230", "15% of 230 = 34.5"),
        ("calculate (3 + 4) * 2", "(3 + 4) * 2 = 14"),
        ("12 x 12", "12 x 12 = 144"),
        ("1,000 * 3", "1,000 * 3 = 3,000"),
        ("2^10", "2^10 = 1,024"),
        ("10 / 4", "10 / 4 = 2.5"),
    ],
)
def test_calculator_answers(text: str, answer: str) -> None:
    route = match(text, S)
    assert route is not None and route.rule == "calc" and route.tool is None
    assert route.answer == answer


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "9**9**9",
        "2**99999",
        "1/0",
        "()",
        "a+1",
        "1e309*10",
        "(" * 50 + "1" + ")" * 50 + "**" * 0,
    ],
)
def test_calculator_is_safe(expr: str) -> None:
    try:
        value = evaluate(expr)
    except CalcError:
        return
    assert abs(value) < float("inf")


def test_number_formatting() -> None:
    assert format_number(3.0) == "3"
    assert format_number(1 / 3) == "0.3333333333"


def test_router_settings_validate_templates() -> None:
    with pytest.raises(ValidationError):
        RouterSettings(search_url="javascript:alert({query})")
    with pytest.raises(ValidationError):
        RouterSettings(search_url="https://example.com/no-placeholder")
    custom = RouterSettings(search_url="http://127.0.0.1:8888/search?q={query}")
    route = match("search for privacy", custom)
    assert route is not None and route.args == {"url": "http://127.0.0.1:8888/search?q=privacy"}


def test_router_match_is_fast() -> None:
    texts = [
        "open firefox",
        "search for cats",
        "what's the weather like?",
        "12 x 12",
        "turn the volume up",
    ] * 40
    durations = []
    for text in texts:
        start = time.perf_counter()
        match(text, S)
        durations.append((time.perf_counter() - start) * 1000)
    durations.sort()
    p95 = durations[int(len(durations) * 0.95)]
    assert p95 < ROUTER_MATCH_BUDGET_MS, f"router p95 {p95:.2f} ms"
