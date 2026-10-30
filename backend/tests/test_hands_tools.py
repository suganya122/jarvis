"""Hands worker tools on the fake backend: ids, risk, trust, refusals. Plus unit tests."""

import pytest

from contracts.common import RiskLevel, Trust
from contracts.worker import WorkerConfig
from workers.hands import tools as T
from workers.hands import uinput
from workers.hands.fake import FakeHands, fixture_window
from workers.hands.keys import KeyError_, display, parse_combo
from workers.hands.risk import key_risk, label_risk
from workers.hands.verify import describe_changes

CONFIG = WorkerConfig(
    allowed_roots=(), denied_paths=(), shell_allowlist=(), max_output_chars=4000, command_timeout_s=5
)


@pytest.fixture
def env() -> T.HandsEnv:
    return T.HandsEnv(CONFIG, hands=FakeHands())


async def ids(env: T.HandsEnv) -> dict[str, str]:
    """Run list_windows + find_elements and return {control name: short id}."""
    windows = await T.ListWindows().run(T.NoArgs(), env)
    assert windows.output.startswith("w1  gtk_fixture_app.py — Hands Fixture  (active)")
    assert windows.trust is Trust.UNTRUSTED
    found = await T.FindElements().run(T.FindArgs(window_id="w1"), env)
    assert found.trust is Trust.UNTRUSTED
    out = {}
    for line in found.output.splitlines():
        short, rest = line.split("  ", 1)
        out[rest.split("'")[1]] = short
    return out


async def test_find_and_click_with_short_ids(env: T.HandsEnv) -> None:
    names = await ids(env)
    assert names["Increment"].startswith("e")
    assessment = T.ClickElement().assess(T.ClickArgs(element_id=names["Increment"]), env)
    assert assessment.risk is RiskLevel.WRITE and "Increment" in assessment.summary
    result = await T.ClickElement().run(T.ClickArgs(element_id=names["Increment"]), env)
    assert result.ok and "Observed: label now 'Count: 1'" in result.output
    assert result.trust is Trust.UNTRUSTED


async def test_button_labels_raise_risk(env: T.HandsEnv) -> None:
    names = await ids(env)
    click = T.ClickElement()
    assert click.assess(T.ClickArgs(element_id=names["Delete all"]), env).risk is RiskLevel.DESTRUCTIVE
    assert click.assess(T.ClickArgs(element_id=names["Send report"]), env).risk is RiskLevel.EXTERNAL_SEND
    menu = T.ChooseMenu().assess(T.MenuArgs(window_id="w1", path=["File", "Say hello"]), env)
    assert menu.risk is RiskLevel.WRITE and "File → Say hello" in menu.summary


async def test_password_field_is_refused_and_hidden(env: T.HandsEnv) -> None:
    names = await ids(env)
    typed = T.TypeText().assess(T.TypeArgs(element_id=names["Password"], text="x"), env)
    assert typed.error == "I don't type into password fields"
    read = await T.ReadElement().run(T.ElementArgs(element_id=names["Password"]), env)
    assert "hunter2" not in read.output and "hidden" in read.output


async def test_type_text_and_read_back(env: T.HandsEnv) -> None:
    names = await ids(env)
    result = await T.TypeText().run(T.TypeArgs(element_id=names["Name"], text="Ada"), env)
    assert result.ok
    read = await T.ReadElement().run(T.ElementArgs(element_id=names["Name"]), env)
    assert read.output == "Ada"


async def test_keys_need_an_active_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uinput, "available", lambda: None)
    hands = FakeHands(windows=[fixture_window(active=False)])
    env = T.HandsEnv(CONFIG, hands=hands)
    await T.ListWindows().run(T.NoArgs(), env)
    tool = T.PressKeys()
    args = T.KeysArgs(window_id="w1", keys="ctrl+s")
    assert tool.assess(args, env).summary == "Press Ctrl+S in gtk_fixture_app.py — Hands Fixture"
    with pytest.raises(Exception, match="isn't the active one"):
        await tool.run(args, env)
    await T.FocusWindow().run(T.WindowArgs(window_id="w1"), env)
    assert (await tool.run(args, env)).ok
    assert hands.windows["fake|w1"].keys == ["ctrl+s"]
    assert tool.assess(T.KeysArgs(window_id="w1", keys="ctrl+w"), env).risk is RiskLevel.DESTRUCTIVE


async def test_unknown_ids_and_missing_uinput(env: T.HandsEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(T.HandsUnavailable, match="call list_windows"):
        T.FindElements().assess(T.FindArgs(window_id="w9"), env)
    await ids(env)
    with pytest.raises(T.HandsUnavailable):
        T.ClickElement().assess(T.ClickArgs(element_id="w1"), env)  # wrong kind
    monkeypatch.setattr(uinput, "available", lambda: "no uinput")
    assert T.PressKeys().assess(T.KeysArgs(window_id="w1", keys="enter"), env).error == "no uinput"


def test_id_map_is_bounded_and_stable() -> None:
    m = T.IdMap()
    first = m.add("e", "real-1", "a")
    assert m.add("e", "real-1", "a") == first
    for i in range(T.MAX_IDS + 10):
        m.add("e", f"x{i}", "b")
    with pytest.raises(T.HandsUnavailable):
        m.resolve(first, "e")


@pytest.mark.parametrize(
    ("combo", "keys"),
    [("Ctrl+Shift+T", ["ctrl", "shift", "t"]), ("cmd+s", ["meta", "s"]), ("Return", ["enter"]),
     ("shift+ctrl+tab", ["ctrl", "shift", "tab"]), ("alt+F4", ["alt", "f4"])],
)  # fmt: skip
def test_parse_combo(combo: str, keys: list[str]) -> None:
    assert parse_combo(combo) == keys


@pytest.mark.parametrize("combo", ["", "ctrl+", "ctrl+a+b", "hyper+x", "a+a", "ctrl+shift+alt+meta+a+b"])
def test_parse_combo_rejects(combo: str) -> None:
    with pytest.raises(KeyError_):
        parse_combo(combo)


def test_key_display_and_risk() -> None:
    assert display(["ctrl", "shift", "t"]) == "Ctrl+Shift+T"
    assert key_risk(parse_combo("alt+f4")) is RiskLevel.DESTRUCTIVE
    assert key_risk(parse_combo("ctrl+s")) is RiskLevel.WRITE
    assert label_risk("Buy now") is RiskLevel.PURCHASE
    assert label_risk("Empty Trash") is RiskLevel.DESTRUCTIVE
    assert label_risk("Open") is RiskLevel.WRITE


def test_uinput_event_encoding() -> None:
    events = uinput.combo_events([29, 31])  # ctrl+s
    decoded = [uinput.EVENT.unpack(e)[2:] for e in events]
    assert decoded == [
        (1, 29, 1),
        (0, 0, 0),
        (1, 31, 1),
        (0, 0, 0),
        (1, 31, 0),
        (0, 0, 0),
        (1, 29, 0),
        (0, 0, 0),
    ]
    wheel = [uinput.EVENT.unpack(e)[2:] for e in uinput.wheel_events("down", 2)]
    assert wheel == [(2, 8, -1), (0, 0, 0), (2, 8, -1), (0, 0, 0)]


def test_describe_changes() -> None:
    from contracts.hands import ElementInfo

    a = {"1": ElementInfo(element_id="1", role="label", name="Count: 0")}
    b = {"1": ElementInfo(element_id="1", role="label", name="Count: 1"),
         "2": ElementInfo(element_id="2", role="dialog", name="Saved")}  # fmt: skip
    assert describe_changes(a, b) == ["label now 'Count: 1' (was 'Count: 0')", "appeared: dialog 'Saved'"]
    assert describe_changes(b, a) == ["label now 'Count: 0' (was 'Count: 1')", "gone: dialog 'Saved'"]


def test_a_click_that_pays_asks_with_what_it_pays_whatever_the_button_says() -> None:
    from workers.hands.risk import MONEY, label_risk

    assert label_risk("Confirm payment") is RiskLevel.PURCHASE
    assert label_risk("Request UberGo ride") is RiskLevel.PURCHASE
    assert label_risk("Book now") is RiskLevel.PURCHASE
    assert label_risk("Continue") is RiskLevel.WRITE
    assert MONEY.search("₹450 to Uber, Visa ending 4242") and MONEY.search("Rs. 120 for the cab")
    assert not MONEY.search("2 tickets for Friday")
