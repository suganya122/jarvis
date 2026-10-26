"""Contract tests every Hands backend must pass: the in-memory fake, and real AT-SPI on Linux.

The real backend drives the GTK fixture app in a private, headless desktop (see
a11y_session.py) and is skipped when GTK/Xvfb/D-Bus aren't installed.
"""

import sys
from collections.abc import Iterator

import pytest

from contracts.hands import ElementInfo, Hands
from tests.a11y_session import a11y_session, missing_requirements
from workers.hands.fake import FakeHands

if sys.platform.startswith("linux"):
    from workers.hands.atspi import AtspiError
    from workers.hands.linux import LinuxHands

    BLOCK_ERRORS: tuple[type[Exception], ...] = (AtspiError,)
else:  # pragma: no cover
    BLOCK_ERRORS = ()

from workers.hands.fake import FakeError

BLOCK_ERRORS = (*BLOCK_ERRORS, FakeError)
REASON = missing_requirements() if sys.platform.startswith("linux") else "Linux only"


@pytest.fixture(scope="module")
def desktop_env() -> Iterator[dict[str, str]]:
    if REASON:
        pytest.skip(REASON)
    with a11y_session() as env:
        yield env


@pytest.fixture(params=["fake", "linux"])
def hands(request: pytest.FixtureRequest) -> Iterator[Hands]:
    if request.param == "fake":
        yield FakeHands()
        return
    env = request.getfixturevalue("desktop_env")
    yield LinuxHands(session_address=env["DBUS_SESSION_BUS_ADDRESS"])


def fixture_window(hands: Hands) -> str:
    return next(w.window_id for w in hands.list_windows() if w.title == "Hands Fixture")


def element(hands: Hands, name: str) -> ElementInfo:
    return next(e for e in hands.find_elements(fixture_window(hands)) if e.name == name)


def test_backend_implements_the_contract(hands: Hands) -> None:
    assert isinstance(hands, Hands)


def test_lists_the_fixture_window_and_controls(hands: Hands) -> None:
    roles = {e.name: e.role for e in hands.find_elements(fixture_window(hands))}
    assert roles["Increment"] == "push button"
    assert roles["Agree"] == "check box"
    assert roles["Name"] == "text"
    assert roles["Password"] == "password text"
    assert "Say hello" in roles


def test_password_text_is_never_exposed(hands: Hands) -> None:
    password = element(hands, "Password")
    assert password.text is None
    assert hands.describe(password.element_id).text is None
    assert not hands.set_text(password.element_id, "x").ok


def test_activate_is_verified_by_observed_change(hands: Hands) -> None:
    result = hands.activate(element(hands, "Increment").element_id)
    assert result.ok and result.evidence["verified"]
    assert any("Count:" in change for change in result.evidence["changes"])


def test_toggle_checkbox(hands: Hands) -> None:
    agree = element(hands, "Agree")
    result = hands.activate(agree.element_id)
    assert result.ok and any("now checked" in c for c in result.evidence["changes"])
    assert "checked" in hands.describe(agree.element_id).states
    hands.activate(agree.element_id)  # leave it unchecked for other tests


def test_set_text_replace_and_append_are_read_back(hands: Hands) -> None:
    name = element(hands, "Name").element_id
    assert hands.set_text(name, "Ada").evidence["text_after"] == "Ada"
    appended = hands.set_text(name, " Lovelace", mode="append")
    assert appended.ok and appended.evidence["text_after"] == "Ada Lovelace"
    assert hands.describe(name).text == "Ada Lovelace"


def test_menu_path(hands: Hands) -> None:
    result = hands.menu(fixture_window(hands), ["File", "Say hello"])
    assert result.ok and any("Hello!" in c for c in result.evidence["changes"])
    assert not hands.menu(fixture_window(hands), ["File", "No such item"]).ok


def test_unsupported_actions_fail_honestly(hands: Hands) -> None:
    from contracts.hands import Rect

    assert not hands.click(10, 10).ok
    assert not hands.move_resize(fixture_window(hands), Rect(x=0, y=0, width=10, height=10)).ok


def test_blocked_apps_are_invisible_and_untouchable(hands: Hands, request: pytest.FixtureRequest) -> None:
    window = fixture_window(hands)
    element_id = element(hands, "Increment").element_id
    if isinstance(hands, FakeHands):
        blocked: Hands = FakeHands(windows=list(hands.windows.values()), blocked_apps=("fixture",))
    else:
        env = request.getfixturevalue("desktop_env")
        blocked = LinuxHands(blocked_apps=["fixture"], session_address=env["DBUS_SESSION_BUS_ADDRESS"])
    assert all(w.title != "Hands Fixture" for w in blocked.list_windows())
    with pytest.raises(BLOCK_ERRORS):
        blocked.find_elements(window)
    with pytest.raises(BLOCK_ERRORS):
        blocked.activate(element_id)


def _focus_field(hands: Hands, name: str) -> str:
    target = element(hands, name)
    if isinstance(hands, FakeHands):
        for window in hands.windows.values():
            for node in window.nodes.values():
                node.states.discard("focused")
        hands.windows["fake|w1"].nodes[target.element_id].states.add("focused")
    else:
        bus, path = target.element_id.split("|", 1)
        hands._c.grab_focus((bus, path))  # type: ignore[attr-defined]
    return target.element_id


def test_insert_text_types_at_the_focused_field(hands: Hands) -> None:
    field = _focus_field(hands, "Name")
    hands.set_text(field, "")
    result = hands.insert_text("Hello world")
    assert result.ok and result.evidence["verified"], result  # verified = landed exactly at the cursor
    assert hands.describe(field).text == "Hello world"


def test_insert_text_refuses_password_fields(hands: Hands) -> None:
    _focus_field(hands, "Password")
    result = hands.insert_text("secret")
    assert not result.ok and "password" in (result.error or "")
