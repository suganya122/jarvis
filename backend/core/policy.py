"""The policy gate that runs before every tool call."""

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel

from contracts.common import Capability, RiskLevel
from contracts.tools import PolicyMode

# Tools that would take over the user's screen (focus, keyboard, new windows).
FOCUS_STEALING_TOOLS = frozenset(
    {"focus_window", "press_keys", "paste_text", "scroll", "open_app", "open_url", "open_path", "insert_text",
     "setup_permission", "browser_handover", "browser_finish"}  # the last two show the browser window
)  # fmt: skip
NEW_CODE = frozenset({"build_ability"})
"""Writes and runs new code: never from a background routine, and from a chat app it asks. At the
computer it's built when the user asks for it (full automation) or says yes to the offer; after
reading outside content only their own yes builds it."""


REACHES_THE_COMPUTER = frozenset(
    {
        Capability.SHELL_RUN,
        Capability.FS_WRITE,
        Capability.INPUT_CONTROL,
        Capability.SCHEDULE,
        Capability.MEMORY,
    }
)
"""After reading outside content (a page, a file, command output), steps with these still ask: a
planted instruction could otherwise run commands, change files, type into the user's apps, or
leave something behind that acts later. Clicking around in its own browser doesn't ask."""
LASTING = frozenset({Capability.MEMORY, Capability.SCHEDULE})
"""With full automation, after reading outside content only what lasts or acts later (a memory, a
routine, something set to start by itself) and commands that send data off the computer still ask:
those are what a planted instruction goes for. Everything else the user asked for just runs."""


class Decision(BaseModel):
    action: Literal["allow", "ask", "deny"]
    reason: str
    can_remember: bool = False
    """Whether 'allow for this chat' may be offered on the approval prompt."""


def decide(
    *,
    tool: str,
    risk: RiskLevel,
    capabilities: Iterable[Capability],
    policies: Mapping[Capability, PolicyMode],
    tainted: bool,
    session_grants: set[str],
    direct: bool = False,
    background: bool = False,
    remote: str | None = None,
    full_automation: bool = False,
    consented: bool = False,
) -> Decision:
    """Order matters: deny beats everything, then the non-negotiable approvals.

    consented: the user's own words just said yes to a question it asked ("Shall I?"), so the steps
    that answer it don't ask again (paying and deleting still do)."""
    caps = list(capabilities)
    denied = [c for c in caps if policies.get(c, "ask") == "deny"]
    if denied:
        return Decision(action="deny", reason=f"{denied[0].value} is turned off in Settings")
    if background and not direct and tool in FOCUS_STEALING_TOOLS:
        return Decision(
            action="deny", reason="it would take over your screen, which background tasks never do"
        )
    if tool in NEW_CODE:
        if background:
            return Decision(
                action="deny", reason="new abilities are only built when you ask, at the computer"
            )
        if remote or not (consented or (full_automation and not tainted)):
            return Decision(action="ask", reason="building an ability writes and runs new code")
        return Decision(action="allow", reason="you asked for it")
    if risk.always_requires_approval:
        return Decision(action="ask", reason=f"{risk.value.replace('_', ' ')} actions always need approval")
    if risk is RiskLevel.EXTERNAL_SEND:
        if not full_automation:
            return Decision(action="ask", reason="external send actions always need approval")
        if remote or background:  # nobody is at the computer, or the request came from a chat account
            return Decision(action="ask", reason="sending asks when it wasn't requested from this computer")
    if remote and risk is not RiskLevel.READ:
        # A chat app account can be lost or hijacked: changes made from it always ask,
        # can't be remembered, and typed commands get no "direct" pass.
        return Decision(
            action="ask", reason=f"the request came from {remote}, and changes from chat apps always ask"
        )
    if direct:
        # The user typed this exact command; nothing the model read can have caused it.
        return Decision(action="allow", reason="you asked for this directly")
    if full_automation:
        sends_out = risk is RiskLevel.EXTERNAL_SEND and Capability.SHELL_RUN in caps
        reaches = bool(set(caps) & LASTING) or sends_out
    else:
        reaches = bool(set(caps) & REACHES_THE_COMPUTER)
    if tainted and risk is not RiskLevel.READ and reaches and not consented:
        return Decision(
            action="ask",
            reason="this chat contains content from outside (files, pages, command output)",
        )
    asking = [c for c in caps if policies.get(c, "ask") == "ask"]
    if asking:
        if tool in session_grants:
            return Decision(action="allow", reason="allowed earlier in this chat")
        return Decision(action="ask", reason=f"{asking[0].value} is set to ask", can_remember=True)
    return Decision(action="allow", reason="allowed by settings")
