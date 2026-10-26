import asyncio

import pytest

from contracts.common import Capability, RiskLevel
from core.approvals import ApprovalBroker
from core.config import default_policies
from core.policy import decide

FS_WRITE = (Capability.FS_WRITE,)


def check(
    risk: RiskLevel,
    caps: tuple[Capability, ...],
    *,
    tainted: bool = False,
    grants: set[str] | None = None,
    **overrides: str,
):  # type: ignore[no-untyped-def]
    policies = default_policies()
    for key, mode in overrides.items():
        policies[Capability(key.replace("_", "."))] = mode  # type: ignore[assignment]
    return decide(
        tool="t",
        risk=risk,
        capabilities=caps,
        policies=policies,
        tainted=tainted,
        session_grants=grants or set(),
    )


def test_reads_inside_allowed_folders_need_no_approval() -> None:
    assert check(RiskLevel.READ, (Capability.FS_READ,)).action == "allow"
    assert check(RiskLevel.READ, ()).action == "allow"


def test_writes_ask_by_default_and_can_be_remembered() -> None:
    decision = check(RiskLevel.WRITE, FS_WRITE)
    assert decision.action == "ask" and decision.can_remember
    assert check(RiskLevel.WRITE, FS_WRITE, grants={"t"}).action == "allow"


@pytest.mark.parametrize("risk", [RiskLevel.DESTRUCTIVE, RiskLevel.EXTERNAL_SEND, RiskLevel.PURCHASE])
def test_high_risk_always_asks_even_when_allowed_or_granted(risk: RiskLevel) -> None:
    decision = check(risk, FS_WRITE, grants={"t"}, fs_write="allow")
    assert decision.action == "ask" and not decision.can_remember


def test_deny_beats_everything() -> None:
    assert check(RiskLevel.DESTRUCTIVE, FS_WRITE, fs_write="deny").action == "deny"
    assert check(RiskLevel.READ, (Capability.FS_READ,), fs_read="deny").action == "deny"


def test_untrusted_content_forces_approval_for_non_reads() -> None:
    decision = check(RiskLevel.WRITE, FS_WRITE, tainted=True, grants={"t"}, fs_write="allow")
    assert decision.action == "ask" and not decision.can_remember
    assert check(RiskLevel.READ, (Capability.FS_READ,), tainted=True).action == "allow"


async def test_broker_resolve_timeout_and_deny_all() -> None:
    broker = ApprovalBroker()
    waiter = asyncio.create_task(broker.wait("a", can_remember=False, timeout_s=5))
    await asyncio.sleep(0)
    assert broker.resolve("a", approved=True, remember=True)
    outcome = await waiter
    assert outcome.approved and not outcome.remember  # remember refused when not offered
    assert not broker.resolve("a", True, False)  # already settled

    timed_out = await broker.wait("b", can_remember=True, timeout_s=0.01)
    assert not timed_out.approved and "in time" in timed_out.reason

    waiter = asyncio.create_task(broker.wait("c", can_remember=True, timeout_s=5))
    await asyncio.sleep(0)
    assert broker.deny_all("stopped") == 1
    assert (await waiter).reason == "stopped"
    assert broker.pending_count == 0
