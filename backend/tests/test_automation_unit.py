"""Pure parts of milestone 8: schedules, automation phrases, the background policy, watcher logic."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from contracts.automation import Schedule, Watcher
from contracts.common import RiskLevel
from core.automation_tools import CreateRoutine, CreateWatcher
from core.config import RouterSettings, default_policies
from core.policy import FOCUS_STEALING_TOOLS, decide
from core.router.rules import match
from core.schedule import describe, next_run, parse_once, parse_recurring
from core.watchers import evaluate

TZ = ZoneInfo("Europe/Berlin")
FRIDAY_9 = datetime(2026, 10, 23, 9, 0, tzinfo=TZ)


# ---- schedules -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("every weekday at 8am", Schedule(kind="weekdays", time="08:00")),
        ("every day at 7:30 pm", Schedule(kind="daily", time="19:30")),
        ("daily at noon", Schedule(kind="daily", time="12:00")),
        ("every monday and friday at 9:30", Schedule(kind="weekly", time="09:30", days=(0, 4))),
        ("every 2 hours", Schedule(kind="interval", every_minutes=120)),
        ("every 15 minutes", Schedule(kind="interval", every_minutes=15)),
    ],
)
def test_recurring_phrases(phrase: str, expected: Schedule) -> None:
    assert parse_recurring(phrase) == expected


@pytest.mark.parametrize("phrase", ["every blursday", "every 2 minutes", "sometimes", "every day at 25:00"])
def test_bad_recurring_phrases(phrase: str) -> None:
    assert parse_recurring(phrase) is None


def test_once_phrases_resolve_relative_to_now() -> None:
    in_20 = parse_once("in 20 minutes", FRIDAY_9)
    assert in_20 is not None and in_20.at == (FRIDAY_9 + timedelta(minutes=20)).timestamp()
    at_5 = parse_once("at 5pm", FRIDAY_9)
    assert at_5 is not None and datetime.fromtimestamp(at_5.at or 0, TZ).hour == 17
    past = parse_once("at 8am", FRIDAY_9)  # already gone today: tomorrow
    assert past is not None and datetime.fromtimestamp(past.at or 0, TZ).day == 24
    tomorrow = parse_once("tomorrow at 9", FRIDAY_9)
    assert tomorrow is not None and datetime.fromtimestamp(tomorrow.at or 0, TZ).day == 24


def test_next_run_skips_weekends_and_is_strictly_later() -> None:
    weekdays = Schedule(kind="weekdays", time="09:00")
    assert next_run(weekdays, FRIDAY_9) == datetime(2026, 10, 26, 9, 0, tzinfo=TZ)  # Monday
    weekly = Schedule(kind="weekly", time="08:00", days=(2,))
    assert next_run(weekly, FRIDAY_9) == datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
    once = Schedule(kind="once", at=FRIDAY_9.timestamp())
    assert next_run(once, FRIDAY_9) is None
    assert next_run(Schedule(kind="interval", every_minutes=30), FRIDAY_9) == FRIDAY_9 + timedelta(minutes=30)


def test_describe_reads_naturally() -> None:
    assert describe(Schedule(kind="weekdays", time="08:00")) == "every weekday at 08:00"
    assert describe(Schedule(kind="interval", every_minutes=120)) == "every 2 hours"
    assert describe(Schedule(kind="weekly", time="09:30", days=(4, 0))) == "every Monday, Friday at 09:30"


# ---- instant phrases ---------------------------------------------------------------------


def route(text: str) -> tuple[str | None, dict[str, Any] | None]:
    found = match(text, RouterSettings())
    return (found.tool, found.args) if found else (None, None)


def test_routine_phrases_route_to_create_routine() -> None:
    tool, args = route("every weekday at 8am, check the build status")
    assert tool == "create_routine"
    assert args == {"prompt": "check the build status", "when": "every weekday at 8am", "action": "run"}
    tool, args = route("remind me every monday at 9 to pay rent")
    assert tool == "create_routine" and args and args["action"] == "notify" and args["prompt"] == "pay rent"
    tool, args = route("remind me to call mom at 5pm")
    assert tool == "create_routine" and args == {"prompt": "call mom", "when": "at 5pm", "action": "notify"}
    tool, args = route("in 20 minutes remind me to stretch")
    assert tool == "create_routine" and args and args["when"] == "in 20 minutes"


def test_watcher_phrases_route_to_create_watcher() -> None:
    assert route("tell me when example.com is back up")[1] == {
        "kind": "url", "target": "example.com", "condition": "up", "text": ""
    }  # fmt: skip
    assert route("let me know when example.com shows sold out")[1] == {
        "kind": "url", "target": "example.com", "condition": "contains", "text": "sold out"
    }  # fmt: skip
    assert route("notify me when ~/Downloads/report.pdf appears")[1] == {
        "kind": "file", "target": "~/Downloads/report.pdf", "condition": "exists", "text": ""
    }  # fmt: skip
    assert route("tell me when a window called Zoom closes")[1] == {
        "kind": "window", "target": "Zoom", "condition": "gone", "text": ""
    }  # fmt: skip


@pytest.mark.parametrize(
    "text", ["every blursday do it", "remind me about stuff", "tell me when you are done"]
)
def test_vague_phrases_go_to_the_model(text: str) -> None:
    assert route(text) == (None, None)


# ---- background policy -------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(FOCUS_STEALING_TOOLS))
def test_background_model_calls_never_take_the_screen(tool: str) -> None:
    def check(**kw: bool) -> str:
        return decide(tool=tool, risk=RiskLevel.READ, capabilities=(), policies=default_policies(),
                      tainted=False, session_grants={tool}, **kw).action  # fmt: skip

    assert check(background=True) == "deny"
    assert check(background=True, direct=True) == "allow"  # the user typed it
    assert check() == "allow"


def test_background_does_not_block_other_tools() -> None:
    policies = default_policies()
    decision = decide(tool="read_text_file", risk=RiskLevel.READ, capabilities=(), policies=policies,
                      tainted=False, session_grants=set(), background=True)  # fmt: skip
    assert decision.action == "allow"


# ---- watcher conditions --------------------------------------------------------------------


def watcher(kind: str, condition: str, target: str = "x", text: str = "") -> Watcher:
    return Watcher(watcher_id="w", name="n", kind=kind, target=target, condition=condition,  # type: ignore[arg-type]
                   text=text, created_at=0)  # fmt: skip


def test_url_conditions() -> None:
    up = {"status": 200, "body": "Now In Stock", "error": ""}
    down = {"status": 0, "body": "", "error": "ConnectError"}
    assert evaluate(watcher("url", "up"), dict(up), {})[0]
    assert not evaluate(watcher("url", "up"), dict(down), {})[0]
    assert evaluate(watcher("url", "down"), dict(down), {}) == (True, "x is down (ConnectError)")
    assert evaluate(watcher("url", "contains", text="in stock"), dict(up), {})[0]


def test_changes_needs_a_baseline_first() -> None:
    w = watcher("url", "changes")
    first = {"status": 200, "body": "v1", "error": ""}
    fired, _ = evaluate(w, first, {})
    assert not fired  # first look only records
    assert not evaluate(w, {"status": 200, "body": "v1", "error": ""}, first)[0]
    assert evaluate(w, {"status": 200, "body": "v2", "error": ""}, first)[0]
    f = watcher("file", "changes")
    before = {"exists": True, "mtime": 1.0, "size": 3}
    evaluate(f, before, {})
    assert evaluate(f, {"exists": True, "mtime": 2.0, "size": 3}, before)[0]


def test_window_conditions_match_titles_case_insensitively() -> None:
    assert evaluate(watcher("window", "exists", "zoom"), {"titles": "Zoom Meeting\nEditor"}, {})[0]
    assert evaluate(watcher("window", "gone", "zoom"), {"titles": "Editor"}, {})[0]


# ---- creation tools ------------------------------------------------------------------------


async def test_create_tools_validate_before_anything_is_saved() -> None:
    def never() -> Any:
        raise AssertionError("should not be reached")

    routine = CreateRoutine(never, lambda: FRIDAY_9)
    bad = await routine.assess({"prompt": "x", "when": "whenever", "action": "run"})
    assert bad.error and "couldn't understand" in bad.error
    ok = await routine.assess({"prompt": "check mail", "when": "every weekday at 8am"})
    assert ok.error is None and ok.summary == "Every weekday at 08:00, run: “check mail”"

    watch = CreateWatcher(never)
    for args, error in [
        ({"kind": "file", "target": "a", "condition": "up"}, "a file watcher can check"),
        ({"kind": "url", "target": "a.com", "condition": "contains"}, "say what text"),
        ({"kind": "url", "target": "ftp://a.com", "condition": "up"}, "only http(s)"),
        ({"kind": "url", "target": "a.com", "condition": "up", "action": "run"}, "say what to do"),
        ({"kind": "url", "target": "a.com", "condition": "up", "every_seconds": 1}, "invalid arguments"),
    ]:
        result = await watch.assess(args)
        assert result.error and error in result.error, (args, result.error)
    good = await watch.assess({"kind": "url", "target": "example.com", "condition": "up"})
    assert good.summary == "When example.com is up, tell you (checked every 60 s)"


def test_sentences_keep_day_and_month_names_capitalized() -> None:
    from core.schedule import sentence

    once = Schedule(kind="once", at=datetime(2026, 10, 26, 9, 0, tzinfo=TZ).timestamp())
    text = sentence(once)
    assert text.startswith("Once, ") and text[6:9].istitle() and text.split()[3].istitle()
