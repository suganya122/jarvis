"""Skill templates, matching, and the learner's safety rules."""

from typing import Any

import pytest

from contracts.skills import ParamSlot, Skill, SkillStep, TraceStep
from skills.learner import NotLearnable, learn
from skills.matcher import match, trigger_pattern
from skills.templates import fill, slots_in

LOCATOR = {"role": "push button", "name": "Increment", "window": {"app": "fixture"}}


def T(tool: str, args: dict[str, Any], **evidence: Any) -> TraceStep:
    return TraceStep(tool=tool, args=args, status="ok", risk="write", evidence=evidence)


def skill(trigger: str, params: tuple[ParamSlot, ...] = (), status: str = "active", sid: str = "s") -> Skill:
    return Skill(
        skill_id=sid,
        name=trigger,
        triggers=(trigger,),
        params=params,
        status=status,  # type: ignore[arg-type]
        steps=(SkillStep(tool="t", args_template={}),),
        signature=sid,
    )


# ---- templates ----------------------------------------------------------------


def test_fill_text_url_and_numbers() -> None:
    template = {"url": "https://x.org/?q={q|url}", "name": "{app}", "n": "{count}", "list": ["{app}!"]}
    out = fill(template, {"q": "cute cats", "app": "Firefox", "count": "3"}, frozenset({"count"}))
    assert out == {"url": "https://x.org/?q=cute+cats", "name": "Firefox", "n": 3, "list": ["Firefox!"]}
    assert slots_in(template) == {"q", "app", "count"}


# ---- matcher --------------------------------------------------------------------


def test_match_extracts_slots_case_insensitively() -> None:
    found = match("Open Firefox and search for cute cats", [skill("open {app} and search for {query}")])
    assert found is not None and found.values == {"app": "Firefox", "query": "cute cats"}


def test_match_strips_quotes_and_rejects_bad_values() -> None:
    sk = skill("write {text} to {path}")
    found = match("write “buy milk” to todo.txt", [sk])
    assert found is not None and found.values["text"] == "buy milk"
    assert match("write " + "x" * 300 + " to a.txt", [sk]) is None


def test_number_slots_only_match_numbers() -> None:
    sk = skill("set volume to {level}", (ParamSlot(name="level", kind="number"),))
    assert match("set volume to 40", [sk]) is not None
    assert match("set volume to loud", [sk]) is None
    assert trigger_pattern("say {x} twice", frozenset()).fullmatch("say  hi   twice") is not None


def test_only_active_enabled_skills_match_and_ambiguity_falls_through() -> None:
    assert match("open firefox", [skill("open {app}", status="candidate")]) is None
    disabled = skill("open {app}").model_copy(update={"enabled": False})
    assert match("open firefox", [disabled]) is None
    two = [skill("open {app} now", sid="a"), skill("open {thing} now", sid="b")]
    assert match("open firefox now", two) is None  # same specificity, different skills
    specific = [skill("open {app}", sid="a"), skill("open {app} in a new window", sid="b")]
    found = match("open firefox in a new window", specific)
    assert found is not None and found.skill.skill_id == "b"


# ---- learner --------------------------------------------------------------------


def test_learns_parameters_from_the_request() -> None:
    draft = learn(
        "open spotify and search for lofi beats",
        [T("open_app", {"name": "spotify"}), T("open_url", {"url": "https://duckduckgo.com/?q=lofi+beats"})],
        tainted_before=False,
    )
    assert draft.trigger == "open {app} and search for {query}"
    assert [s.args_template for s in draft.steps] == [
        {"name": "{app}"},
        {"url": "https://duckduckgo.com/?q={query|url}"},
    ]


def test_hands_targets_become_locators_and_locate_steps_are_dropped() -> None:
    draft = learn(
        "click increment in the fixture",
        [
            T("list_windows", {}),
            T("find_elements", {"window_id": "w1"}),
            T("click_element", {"element_id": "e3"}, verified=True, target={"element": LOCATOR}),
        ],
        tainted_before=False,
    )
    assert len(draft.steps) == 1
    assert draft.steps[0].args_template == {"element_id": LOCATOR}
    assert draft.steps[0].checks == ("ok", "verified")


def test_signature_is_stable_across_parameter_values() -> None:
    a = learn("open spotify", [T("open_app", {"name": "spotify"})], False)
    b = learn("open firefox", [T("open_app", {"name": "firefox"})], False)
    assert a.signature == b.signature and a.trigger == b.trigger == "open {app}"


@pytest.mark.parametrize(
    ("request_text", "trace", "tainted", "reason"),
    [
        ("open spotify", [T("open_app", {"name": "spotify"})], True, "outside content"),
        (
            "summarize notes",
            [T("read_text_file", {"path": "~/n"}), T("open_app", {"name": "x"})],
            False,
            "read",
        ),
        ("do nothing", [T("list_windows", {})], False, "nothing was changed"),
        (
            "open spotify",
            [TraceStep(tool="open_app", args={"name": "spotify"}, status="denied", risk="w")],
            False,
            "succeeded",
        ),
        (
            "click it",
            [T("click_element", {"element_id": "e1"}, verified=False, target={"element": LOCATOR})],
            False,
            "no visible effect",
        ),
        ("click it", [T("click_element", {"element_id": "e1"})], False, "can't be found again"),
        (
            "write a poem to poem.txt",
            [
                T(
                    "write_text_file",
                    {"path": "~/poem.txt", "content": "Roses are red, violets are blue, this poem is new"},
                )
            ],
            False,
            "fresh content",
        ),
        ("log in with my password", [T("type_text", {"text": "hunter2"})], False, "passwords"),
        ("store key", [T("type_text", {"text": "sk-ant-abcdefghijklmnop"})], False, "secret"),
        ("{x}", [T("open_app", {"name": "x"})], False, "too generic"),
    ],
)
def test_not_learnable(request_text: str, trace: list[TraceStep], tainted: bool, reason: str) -> None:
    with pytest.raises(NotLearnable, match=reason):
        learn(request_text, trace, tainted)


def test_enum_values_are_not_split_into_slots() -> None:
    draft = learn("set volume up a bit", [T("media_control", {"action": "volume_up"})], False)
    assert draft.steps[0].args_template == {"action": "volume_up"}


def test_url_domains_stay_constant_and_query_slots_are_always_encoded() -> None:
    one_word = learn(
        "show me wikipedia results for tides",
        [T("browse", {"url": "https://en.wikipedia.org/w/index.php?search=tides"})],
        False,
    )
    two_words = learn(
        "show me wikipedia results for rust traits",
        [T("browse", {"url": "https://en.wikipedia.org/w/index.php?search=rust+traits"})],
        False,
    )
    assert one_word.steps[0].args_template == {
        "url": "https://en.wikipedia.org/w/index.php?search={query|url}"
    }
    assert one_word.signature == two_words.signature  # so the two runs merge into one skill
    whole_host = learn("visit python.org for me", [T("browse", {"url": "https://python.org"})], False)
    assert whole_host.steps[0].args_template == {"url": "https://{query}"}
