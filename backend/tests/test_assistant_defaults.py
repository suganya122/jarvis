"""Out of the box it's full automation: it does what it's asked, sending messages included, and
asks first only before spending money or deleting. Sends still ask when nobody is at this computer
(a chat app's request, an unattended routine)."""

import pytest

from contracts.common import Capability, RiskLevel
from core.config import Settings, ToolSettings
from core.policy import decide

pytestmark = pytest.mark.real_defaults


def ask(
    tool: str,
    risk: RiskLevel,
    *caps: Capability,
    tainted: bool = False,
    remote: str | None = None,
    background: bool = False,
) -> str:
    tools = Settings().tools
    return decide(
        tool=tool,
        risk=risk,
        capabilities=caps,
        policies=tools.policies,
        tainted=tainted,
        session_grants=set(),
        remote=remote,
        background=background,
        full_automation=tools.full_automation,
    ).action


def test_everyday_steps_just_happen() -> None:
    assert ask("browser_click", RiskLevel.WRITE, Capability.BROWSER) == "allow"
    assert ask("open_app", RiskLevel.WRITE, Capability.INPUT_CONTROL) == "allow"
    assert ask("write_text_file", RiskLevel.WRITE, Capability.FS_WRITE) == "allow"
    assert ask("mac_automation", RiskLevel.WRITE, Capability.INPUT_CONTROL) == "allow"


def test_sending_a_message_just_happens_but_paying_and_deleting_ask() -> None:
    # "send a WhatsApp message to Srinith saying I'm out of station": Enter in a chat app is a send
    assert ask("press_keys", RiskLevel.EXTERNAL_SEND, Capability.INPUT_CONTROL) == "allow"
    assert ask("mac_automation", RiskLevel.EXTERNAL_SEND, Capability.INPUT_CONTROL) == "allow"
    assert ask("browser_click", RiskLevel.PURCHASE, Capability.BROWSER, Capability.PURCHASE) == "ask"
    assert ask("mac_automation", RiskLevel.DESTRUCTIVE, Capability.INPUT_CONTROL) == "ask"


def test_sends_still_ask_when_nobody_is_at_this_computer() -> None:
    assert ask("press_keys", RiskLevel.EXTERNAL_SEND, Capability.INPUT_CONTROL, remote="telegram") == "ask"
    assert ask("mac_automation", RiskLevel.EXTERNAL_SEND, Capability.INPUT_CONTROL, background=True) == "ask"
    # (and a background routine can't press keys at all: it never takes over the screen)
    assert ask("press_keys", RiskLevel.EXTERNAL_SEND, Capability.INPUT_CONTROL, background=True) == "deny"


def test_with_full_automation_off_every_send_asks_again() -> None:
    tools = ToolSettings(full_automation=False)
    decision = decide(
        tool="press_keys",
        risk=RiskLevel.EXTERNAL_SEND,
        capabilities=(Capability.INPUT_CONTROL,),
        policies=tools.policies,
        tainted=False,
        session_grants=set(),
        full_automation=tools.full_automation,
    )
    assert decision.action == "ask"


def test_after_reading_outside_content_only_commands_and_file_changes_ask() -> None:
    # it read WhatsApp's window or a web page, then operates apps: no questions
    assert ask("browser_click", RiskLevel.WRITE, Capability.BROWSER, tainted=True) == "allow"
    assert ask("type_text", RiskLevel.WRITE, Capability.INPUT_CONTROL, tainted=True) == "allow"
    assert ask("press_keys", RiskLevel.EXTERNAL_SEND, Capability.INPUT_CONTROL, tainted=True) == "allow"
    # a planted instruction must not run commands or change files unasked
    assert ask("run_command", RiskLevel.WRITE, Capability.SHELL_RUN, tainted=True) == "ask"
    assert ask("write_text_file", RiskLevel.WRITE, Capability.FS_WRITE, tainted=True) == "ask"
    # and what lasts or acts later: a web page must not plant a "memory" or a routine
    assert ask("remember", RiskLevel.WRITE, Capability.MEMORY, tainted=True) == "ask"
    assert ask("remember", RiskLevel.WRITE, Capability.MEMORY, tainted=False) == "allow"
    assert ask("create_routine", RiskLevel.WRITE, Capability.SCHEDULE, tainted=True) == "ask"


def test_settings_saved_with_the_old_ask_everything_default_are_upgraded() -> None:
    old = {c: ("allow" if c is Capability.FS_READ else "ask") for c in Capability}
    assert ToolSettings(policies=old).policies == Settings().tools.policies
    chosen = {**Settings().tools.policies, Capability.BROWSER: "ask"}  # the user's own choice stays
    assert ToolSettings(policies=chosen).policies[Capability.BROWSER] == "ask"


def test_settings_saved_with_the_0_1_10_defaults_are_upgraded_too() -> None:
    second = {
        c: ("ask" if c in (Capability.SEND_EXTERNAL, Capability.PURCHASE) else "allow") for c in Capability
    }
    assert ToolSettings(policies=second).policies == Settings().tools.policies
    assert Settings().tools.policies[Capability.SEND_EXTERNAL] == "allow"
    assert Settings().tools.policies[Capability.PURCHASE] == "ask"
    assert Settings().tools.full_automation is True
