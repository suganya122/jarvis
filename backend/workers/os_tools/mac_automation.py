"""macOS: reach inside apps with AppleScript (Calendar, Reminders, Mail, Notes, Music, Finder,
System Settings…), the way the Mac lets one app script another.

The risk is read from the script itself, never just from what the model says it does:
a script that only reads runs at once; one that changes something is a "yes/no"; one that sends,
deletes, or quits apps needs a click; `do shell script` is refused (commands go through
run_command and its allowlist). macOS asks once per app ("<app name> wants to control Calendar").
"""

import asyncio
import re
import subprocess
import sys

from pydantic import Field

from contracts import APP_NAME
from contracts.common import Capability, RiskLevel, Trust
from contracts.tools import ToolAssessment, ToolResult
from workers.os_tools.base import Args, Env, WorkerTool

TIMEOUT_S = 30.0
TELL = re.compile(r'tell\s+application\s+(?:id\s+)?"([^"]+)"', re.IGNORECASE)
REFUSED = re.compile(r"\bdo\s+shell\s+script\b|\brun\s+script\b|\bstore\s+script\b|\bload\s+script\b", re.I)
SENDS = re.compile(r"\bsend\b|\breply\b|\bforward\b|\bpost\b|\bshare\b", re.I)
DESTROYS = re.compile(
    r"\bdelete\b|\bremove\b|\berase\b|\bempty\s+trash\b|\bquit\b|\bshut\s+down\b|\brestart\b", re.I
)
PAYS = re.compile(r"\bbuy\b|\bpurchase\b|\bcheckout\b|\bpay\b", re.I)
CHANGES = re.compile(
    r"\bmake\b|\bset\s+(?:the\s+)?\w+(?:\s+\w+)?\s+of\b|\bset\s+volume\b|\bmove\b|\bduplicate\b|"
    r"\bsave\b|\bclose\b|\bcreate\b|\bopen\b|\bactivate\b|\bplay\b|\bpause\b|\bkeystroke\b|\bkey\s+code\b|\bclick\b|\bimport\b|\badd\b|\bmark\b|\bcomplete\b",
    re.I,
)
STRINGS = re.compile(r'"(?:[^"\\]|\\.)*"')
"""`set x to …` only names a value; `set name of event 1 to …` changes the app's data."""


class AutomationArgs(Args):
    app: str = Field(
        min_length=1, max_length=80, description="The app the script works with, e.g. 'Calendar'."
    )
    purpose: str = Field(
        min_length=1,
        max_length=160,
        description="What it does, in a few words the user will see and approve.",
    )
    script: str = Field(
        min_length=1,
        max_length=8000,
        description="AppleScript. Return a short text result (e.g. a list of event titles and times).",
    )


def risk_of(script: str) -> RiskLevel:
    """From the script's own words (quoted text aside, so an event called "Delete me" is just a name)."""
    code = STRINGS.sub('""', script)
    if PAYS.search(code):
        return RiskLevel.PURCHASE
    if SENDS.search(code):
        return RiskLevel.EXTERNAL_SEND
    if DESTROYS.search(code):
        return RiskLevel.DESTRUCTIVE
    if CHANGES.search(code):
        return RiskLevel.WRITE
    return RiskLevel.READ


class MacAutomation(WorkerTool[AutomationArgs]):
    name = "mac_automation"
    description = (
        "macOS only: run a short AppleScript to read or change things inside apps: Calendar events, "
        "Reminders, Notes, Mail, Music, Finder, Contacts, System Settings. Prefer reading first. "
        "The user approves anything that changes, sends or deletes. Not for shell commands."
    )
    args_model = AutomationArgs
    risk = RiskLevel.WRITE
    capabilities = (Capability.INPUT_CONTROL,)

    def assess(self, args: AutomationArgs, env: Env) -> ToolAssessment:
        risk = risk_of(args.script)
        summary = f"{args.purpose} ({args.app})"
        if sys.platform != "darwin":
            return ToolAssessment(risk=risk, summary=summary, error="AppleScript is only on macOS")
        if REFUSED.search(STRINGS.sub('""', args.script)):
            return ToolAssessment(
                risk=risk, summary=summary, error="shell commands go through run_command, not AppleScript"
            )
        targets = {args.app, *TELL.findall(args.script)}
        blocked = [t for t in targets for b in env.config.blocked_apps if b and b.casefold() in t.casefold()]
        if blocked:
            return ToolAssessment(risk=risk, summary=summary, error=f"{blocked[0]} is on the blocklist")
        return ToolAssessment(risk=risk, summary=summary)

    async def run(self, args: AutomationArgs, env: Env) -> ToolResult:
        try:
            done = await asyncio.to_thread(
                subprocess.run,
                ["osascript", "-"],
                input=args.script,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output=f"{args.app} didn't answer within {TIMEOUT_S:.0f} seconds.")
        limit = env.config.max_output_chars
        if done.returncode != 0:
            error = done.stderr.strip()[:500]
            if "-1743" in error or "Not authorized" in error:
                error = (
                    f"macOS hasn't allowed {APP_NAME} to control {args.app}: System Settings → "
                    f"Privacy & Security → Automation → {APP_NAME} → turn on {args.app}."
                )
            return ToolResult(ok=False, output=error or "The script failed.", evidence={"app": args.app})
        output = done.stdout.strip() or "Done (no result)."
        return ToolResult(
            ok=True,
            output=output[:limit],
            trust=Trust.UNTRUSTED,  # what apps hold (mail, invites, notes) can come from anyone
            evidence={"app": args.app, "chars": len(output)},
        )
