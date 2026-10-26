"""Pending approval requests, resolved by the UI, a timeout, or the kill switch."""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass

from pydantic import BaseModel


class ApprovalOutcome(BaseModel):
    approved: bool
    remember: bool
    reason: str


@dataclass
class _Pending:
    future: asyncio.Future[ApprovalOutcome]
    can_remember: bool


class ApprovalBroker:
    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def new_id(self) -> str:
        return uuid.uuid4().hex

    async def wait(self, approval_id: str, can_remember: bool, timeout_s: float) -> ApprovalOutcome:
        future: asyncio.Future[ApprovalOutcome] = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = _Pending(future, can_remember)
        try:
            return await asyncio.wait_for(future, timeout_s)
        except TimeoutError:
            return ApprovalOutcome(approved=False, remember=False, reason="no answer in time")
        finally:
            self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool, remember: bool) -> bool:
        pending = self._pending.get(approval_id)
        if pending is None or pending.future.done():
            return False
        reason = "approved by you" if approved else "denied by you"
        outcome = ApprovalOutcome(
            approved=approved, remember=remember and pending.can_remember, reason=reason
        )
        pending.future.set_result(outcome)
        return True

    def deny_all(self, reason: str) -> int:
        count = 0
        for pending in list(self._pending.values()):
            if not pending.future.done():
                with contextlib.suppress(asyncio.InvalidStateError):
                    pending.future.set_result(ApprovalOutcome(approved=False, remember=False, reason=reason))
                    count += 1
        return count

    @property
    def pending_count(self) -> int:
        return len(self._pending)
