"""AppleScript automation's risk comes from the script's own words: reading runs at once, changes
are a yes/no, sending/deleting/paying need a click, shell commands and blocked apps are refused."""

import sys
from pathlib import Path

import pytest

from contracts.common import RiskLevel
from contracts.worker import WorkerConfig
from workers.os_tools.base import Env
from workers.os_tools.mac_automation import AutomationArgs, MacAutomation, risk_of

UPCOMING = """
set out to ""
tell application "Calendar"
    set theEvents to every event of calendar "Work" whose start date > (current date)
    repeat with e in theEvents
        set out to out & summary of e & " at " & (start date of e as string) & linefeed
    end repeat
end tell
return out
"""


@pytest.mark.parametrize(
    ("script", "risk"),
    [
        (UPCOMING, RiskLevel.READ),  # `set x to …` is only a variable
        (
            'tell application "Calendar" to set summary of event 1 of calendar "Work" to "Lunch"',
            RiskLevel.WRITE,
        ),
        (
            'tell application "Reminders" to make new reminder with properties {name:"Call Ravi"}',
            RiskLevel.WRITE,
        ),
        ('tell application "Mail" to send (make new outgoing message)', RiskLevel.EXTERNAL_SEND),
        ('tell application "Calendar" to delete event 1 of calendar "Work"', RiskLevel.DESTRUCTIVE),
        (
            'tell application "Notes" to get name of every note whose name contains "delete me"',
            RiskLevel.READ,
        ),
    ],
)
def test_risk_comes_from_what_the_script_does(script: str, risk: RiskLevel) -> None:
    assert risk_of(script) is risk


def config(tmp_path: Path) -> WorkerConfig:
    return WorkerConfig(
        allowed_roots=(str(tmp_path),),
        denied_paths=(),
        shell_allowlist=(),
        max_output_chars=4000,
        command_timeout_s=5,
        blocked_apps=("1Password",),
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="AppleScript runs only on macOS")
def test_shell_commands_and_blocked_apps_are_refused_on_a_mac(tmp_path: Path) -> None:
    tool, env = MacAutomation(), Env(config(tmp_path))
    shell = AutomationArgs(app="Finder", purpose="x", script='do shell script "rm -rf ~"')
    assert "run_command" in (tool.assess(shell, env).error or "")
    vault = AutomationArgs(app="Finder", purpose="x", script='tell application "1Password" to activate')
    assert "blocklist" in (tool.assess(vault, env).error or "")
    assert (
        tool.assess(AutomationArgs(app="Calendar", purpose="List meetings", script=UPCOMING), env).error
        is None
    )


@pytest.mark.skipif(sys.platform == "darwin", reason="elsewhere it explains it's Mac-only")
def test_elsewhere_it_says_it_is_mac_only(tmp_path: Path) -> None:
    args = AutomationArgs(app="Calendar", purpose="List meetings", script=UPCOMING)
    assert "macOS" in (MacAutomation().assess(args, Env(config(tmp_path))).error or "")
