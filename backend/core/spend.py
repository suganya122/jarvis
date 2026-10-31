"""Spend caps: once today's or this month's estimated Claude cost reaches its cap, every new
model request is refused (chat, background tasks, routines, speculation, dictation clean-up);
one already under way is finished, so nothing stops halfway. Instant commands, skills,
reminders and watchers need no model and keep working."""

from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Literal

from contracts.model import ModelError, ModelEvent, ModelProvider, ModelRequest, ProviderStatus
from core.config import Settings
from core.store import CostSummary, Store

STARTED_KEPT = 200


class SpendStatus(CostSummary):
    daily_cap_usd: float | None
    monthly_cap_usd: float | None
    capped: Literal["daily", "monthly"] | None
    """Which cap is reached (model calls are refused), if any."""
    warning: bool
    """At or past the warning fraction of a cap."""


def _money(usd: float) -> str:
    return f"${usd:,.2f}"


class SpendGuard:
    def __init__(self, store: Store, settings: Callable[[], Settings]) -> None:
        self._store = store
        self._settings = settings

    async def status(self, now: datetime | None = None) -> SpendStatus:
        summary = await self._store.cost_summary(now)
        caps = self._settings().spending
        capped: Literal["daily", "monthly"] | None = None
        if caps.monthly_cap_usd is not None and summary.month_usd >= caps.monthly_cap_usd:
            capped = "monthly"
        elif caps.daily_cap_usd is not None and summary.today_usd >= caps.daily_cap_usd:
            capped = "daily"
        warning = any(
            cap is not None and spent >= cap * caps.warn_at
            for spent, cap in (
                (summary.today_usd, caps.daily_cap_usd),
                (summary.month_usd, caps.monthly_cap_usd),
            )
        )
        return SpendStatus(
            **summary.model_dump(),
            daily_cap_usd=caps.daily_cap_usd,
            monthly_cap_usd=caps.monthly_cap_usd,
            capped=capped,
            warning=warning,
        )

    async def refusal(self) -> ModelError | None:
        """None when a model call may go ahead."""
        status = await self.status()
        if status.capped == "monthly":
            spent, cap, period = status.month_usd, status.monthly_cap_usd, "monthly"
        elif status.capped == "daily":
            spent, cap, period = status.today_usd, status.daily_cap_usd, "daily"
        else:
            return None
        return ModelError(
            code="spend_cap",
            message=(
                f"Your {period} spending cap is reached ({_money(spent)} of {_money(cap or 0)}), "
                "so I won't call Claude. Instant commands, skills, reminders and watchers still work. "
                "You can raise the cap in Settings → Spending."
            ),
        )


class CappedProvider:
    """Wraps whichever provider is active: the one place every model call passes through."""

    def __init__(self, inner: Callable[[], ModelProvider], guard: SpendGuard) -> None:
        self._inner = inner
        self._guard = guard
        self._started: OrderedDict[str, None] = OrderedDict()
        """Requests already under way: the cap stops new ones, never one halfway through."""

    @property
    def name(self) -> str:
        return self._inner().name

    async def status(self) -> ProviderStatus:
        return await self._inner().status()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if request.trace_id not in self._started:
            refusal = await self._guard.refusal()
            if refusal is not None:
                yield refusal
                return
            self._started[request.trace_id] = None
            while len(self._started) > STARTED_KEPT:
                self._started.popitem(last=False)
        async for event in self._inner().stream(request):
            yield event
