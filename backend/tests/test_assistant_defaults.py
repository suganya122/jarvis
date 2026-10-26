"""Out of the box it acts like a real assistant: it just does what it's asked, and asks first only
before sending something to someone, spending money, or anything that deletes or can't be undone."""

import pytest

from contracts.common import Capability, RiskLevel
from core.config import Settings, ToolSettings
from core.policy import decide

pytestmark = pytest.mark.real_defaults


def ask(tool: str, risk: RiskLevel, *caps: Capability, tainted: bool = False) -> str:
    return decide(
        tool=tool,
        risk=risk,
        capabilities=caps,
        policies=Settings().tools.policies,
        tainted=tainted,
        session_grants=set(),
    ).action


def test_everyday_steps_just_happen() -> None:
    assert ask("browser_click", RiskLevel.WRITE, Capability.BROWSER) == "allow"
    assert ask("open_app", RiskLevel.WRITE, Capability.INPUT_CONTROL) == "allow"
    assert ask("write_text_file", RiskLevel.WRITE, Capability.FS_WRITE) == "allow"
    assert ask("mac_automation", RiskLevel.WRITE, Capability.INPUT_CONTROL) == "allow"


def test_sending_paying_and_deleting_ask_first() -> None:
    assert ask("press_keys", RiskLevel.EXTERNAL_SEND, Capability.INPUT_CONTROL) == "ask"
    assert ask("browser_click", RiskLevel.PURCHASE, Capability.BROWSER, Capability.PURCHASE) == "ask"
    assert ask("mac_automation", RiskLevel.DESTRUCTIVE, Capability.INPUT_CONTROL) == "ask"


def test_after_reading_a_page_it_still_clicks_in_its_browser_but_asks_before_touching_the_computer() -> None:
    # e.g. it read YouTube's results, then plays the video: no question
    assert ask("browser_click", RiskLevel.WRITE, Capability.BROWSER, tainted=True) == "allow"
    # a planted instruction could otherwise run commands or type into the user's apps
    assert ask("run_command", RiskLevel.WRITE, Capability.SHELL_RUN, tainted=True) == "ask"
    assert ask("type_text", RiskLevel.WRITE, Capability.INPUT_CONTROL, tainted=True) == "ask"


def test_settings_saved_with_the_old_ask_everything_default_are_upgraded() -> None:
    old = {c: ("allow" if c is Capability.FS_READ else "ask") for c in Capability}
    assert ToolSettings(policies=old).policies == Settings().tools.policies
    chosen = {**Settings().tools.policies, Capability.BROWSER: "ask"}  # the user's own choice stays
    assert ToolSettings(policies=chosen).policies[Capability.BROWSER] == "ask"
