"""Skill contract: a parameterized, verified plan that replays without the LLM.

Args templates use `{slot}` (raw) or `{slot|url}` (URL-encoded) placeholders, filled
from the matching request. Hands targets are stored as locators, never session ids.
"""

from typing import Any, Literal

from contracts.common import ContractModel

Check = Literal["ok", "verified"]
"""'ok': the tool reported success. 'verified': it also observed the effect."""
SkillStatus = Literal["candidate", "active", "demoted"]


class ParamSlot(ContractModel):
    name: str
    kind: Literal["text", "number"] = "text"
    example: str = ""


class SkillStep(ContractModel):
    tool: str
    args_template: dict[str, Any]
    checks: tuple[Check, ...] = ("ok",)


class Skill(ContractModel):
    skill_id: str
    name: str
    triggers: tuple[str, ...]
    """Phrases with {slot} placeholders, e.g. 'search youtube for {query}'."""
    params: tuple[ParamSlot, ...]
    steps: tuple[SkillStep, ...]
    signature: str
    """Hash of the step shape; runs with the same shape merge into one skill."""
    status: SkillStatus = "candidate"
    enabled: bool = True
    verified_successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    created_at: float = 0.0
    last_used_at: float | None = None


class SkillNote(ContractModel):
    """What happened with skills on this turn, shown under the reply."""

    event: Literal["learned", "progress", "promoted", "replayed", "failed", "demoted"]
    skill_id: str
    name: str
    successes: int
    needed: int


class TraceStep(ContractModel):
    """One executed tool call, as the learner sees it."""

    tool: str
    args: dict[str, Any]
    status: str
    risk: str
    trust: str = "user"
    """'untrusted' when the result carried outside content (screen, file, web text)."""
    evidence: dict[str, Any] = {}
