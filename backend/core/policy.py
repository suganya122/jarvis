"""The policy gate that runs before every tool call."""

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel

from contracts.common import Capability, RiskLevel
from contracts.tools import PolicyMode

# Tools that would take over the user's screen (focus, keyboard, new windows).
FOCUS_STEALING_TOOLS = frozenset(
    {"focus_window", "press_keys", "scroll", "open_app", "open_url", "open_path", "insert_text",
     "browser_handover", "browser_finish"}  # these two show the browser window to the user
)  # fmt: skip


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
) -> Decision:
    """Order matters: deny beats everything, then the non-negotiable approvals."""
    caps = list(capabilities)
    denied = [c for c in caps if policies.get(c, "ask") == "deny"]
    if denied:
        return Decision(action="deny", reason=f"{denied[0].value} is turned off in Settings")
    if background and not direct and tool in FOCUS_STEALING_TOOLS:
        return Decision(
            action="deny", reason="it would take over your screen, which background tasks never do"
        )
    if risk.always_requires_approval:
        return Decision(action="ask", reason=f"{risk.value.replace('_', ' ')} actions always need approval")
    if remote and risk is not RiskLevel.READ:
        # A chat app account can be lost or hijacked: changes made from it always ask,
        # can't be remembered, and typed commands get no "direct" pass.
        return Decision(
            action="ask", reason=f"the request came from {remote}, and changes from chat apps always ask"
        )
    if direct:
        # The user typed this exact command; nothing the model read can have caused it.
        return Decision(action="allow", reason="you asked for this directly")
    if tainted and risk is not RiskLevel.READ:
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
