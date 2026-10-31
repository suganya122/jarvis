"""Out of the box it's full automation: it does what it's asked, sending messages included, and
asks first only before spending money or deleting. Sends still ask when nobody is at this computer
(a chat app's request, an unattended routine)."""

import pytest

from contracts.common import Capability, RiskLevel
from core.config import Settings, ToolSettings, upgrade_saved
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


def test_after_reading_outside_content_only_what_lasts_or_sends_data_out_asks() -> None:
    """Checking flight times read a page, and then every command asked "yes or no" (a command's
    own output counts as outside content, so the second one always did). What a planted instruction
    goes for still asks: something that lasts or acts later, and a command sending data away."""
    # it read WhatsApp's window or a web page, then operates apps, runs commands, writes files
    assert ask("browser_click", RiskLevel.WRITE, Capability.BROWSER, tainted=True) == "allow"
    assert ask("type_text", RiskLevel.WRITE, Capability.INPUT_CONTROL, tainted=True) == "allow"
    assert ask("press_keys", RiskLevel.EXTERNAL_SEND, Capability.INPUT_CONTROL, tainted=True) == "allow"
    assert ask("run_shell", RiskLevel.WRITE, Capability.SHELL_RUN, tainted=True) == "allow"
    assert ask("write_text_file", RiskLevel.WRITE, Capability.FS_WRITE, tainted=True) == "allow"
    # a web page must not plant a "memory" or a routine, or send files away with a command
    assert ask("remember", RiskLevel.WRITE, Capability.MEMORY, tainted=True) == "ask"
    assert ask("remember", RiskLevel.WRITE, Capability.MEMORY, tainted=False) == "allow"
    assert ask("create_routine", RiskLevel.WRITE, Capability.SCHEDULE, tainted=True) == "ask"
    assert ask("run_shell", RiskLevel.EXTERNAL_SEND, Capability.SHELL_RUN, tainted=True) == "ask"
    assert ask("run_shell", RiskLevel.EXTERNAL_SEND, Capability.SHELL_RUN, tainted=False) == "allow"
    # paying and deleting ask whatever happened before
    assert ask("run_shell", RiskLevel.DESTRUCTIVE, Capability.SHELL_RUN) == "ask"


def test_a_yes_to_its_own_question_is_the_approval() -> None:
    """ "Shall I?" "Yes." — that yes is the approval: the steps that answer it don't ask again.
    Paying and deleting still do, and so does anything from a chat app."""
    tools = Settings().tools

    def asked(tool: str, risk: RiskLevel, *caps: Capability, **kw: object) -> str:
        return decide(tool=tool, risk=risk, capabilities=caps, policies=tools.policies, tainted=True,
                      session_grants=set(), full_automation=True, consented=True, **kw).action  # type: ignore[arg-type]  # fmt: skip

    assert asked("remember", RiskLevel.WRITE, Capability.MEMORY) == "allow"
    assert asked("run_shell", RiskLevel.EXTERNAL_SEND, Capability.SHELL_RUN) == "allow"
    assert asked("run_shell", RiskLevel.DESTRUCTIVE, Capability.SHELL_RUN) == "ask"
    assert asked("click_element", RiskLevel.PURCHASE, Capability.INPUT_CONTROL) == "ask"
    assert asked("remember", RiskLevel.WRITE, Capability.MEMORY, remote="telegram") == "ask"


def saved_with(policies: dict[Capability, str], version: int = 2) -> Settings:
    data = {"defaults_version": version, "tools": {"policies": {c.value: m for c, m in policies.items()}}}
    return Settings.model_validate(upgrade_saved(data))


def test_settings_saved_with_the_old_ask_everything_default_are_upgraded() -> None:
    old = {c: ("allow" if c is Capability.FS_READ else "ask") for c in Capability}
    assert saved_with(old).tools.policies == Settings().tools.policies
    chosen = {**Settings().tools.policies, Capability.BROWSER: "ask"}  # the user's own choice stays
    assert saved_with(chosen).tools.policies[Capability.BROWSER] == "ask"
    # Once upgraded, choosing that same strict setting yourself is kept: it isn't "upgraded" again.
    assert saved_with(old, version=3).tools.policies == old
    assert ToolSettings(policies=old).policies == old


def test_settings_saved_with_the_0_1_10_defaults_are_upgraded_too() -> None:
    second = {
        c: ("ask" if c in (Capability.SEND_EXTERNAL, Capability.PURCHASE) else "allow") for c in Capability
    }
    assert saved_with(second).tools.policies == Settings().tools.policies
    assert Settings().tools.policies[Capability.SEND_EXTERNAL] == "allow"
    assert Settings().tools.policies[Capability.PURCHASE] == "ask"
    assert Settings().tools.full_automation is True


def test_saved_limits_that_stopped_payments_halfway_are_raised_once() -> None:
    """Paying on a real site stopped with "this is taking more work than I do in one go": every
    step sends the page again, and that cached part counted in full."""
    old = Settings.model_validate(
        upgrade_saved({"defaults_version": 3, "agent": {"max_steps": 15, "max_turn_tokens": 200_000}})
    )
    assert (old.agent.max_steps, old.agent.max_turn_tokens) == (40, 400_000)
    chosen = Settings.model_validate(upgrade_saved({"defaults_version": 3, "agent": {"max_steps": 8}}))
    assert chosen.agent.max_steps == 8  # a limit the user set is kept
    from contracts.model import Usage
    from core.turn import Turn

    turn = Turn.__new__(Turn)
    turn.usage = Usage(
        input_tokens=2_000, output_tokens=1_000, cache_read_tokens=900_000, cache_write_tokens=20_000
    )
    assert turn.work_tokens == 113_000  # under the limit: the rereads cost a tenth
