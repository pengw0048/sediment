"""Local-only LLM cost budget tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime


@dataclass(kw_only=True, slots=True)
class CostBudget:
    """Daily USD budget gate."""

    daily_usd_budget: float = 1.0
    spent_usd: float = 0.0
    day: date = field(default_factory=lambda: datetime.now(tz=UTC).date())

    def can_spend(self, amount: float) -> bool:
        """Return whether a call may spend this much."""
        today = datetime.now(tz=UTC).date()
        if today != self.day:
            self.day = today
            self.spent_usd = 0.0
        return self.spent_usd + amount <= self.daily_usd_budget

    def record(self, amount: float) -> None:
        """Record local estimated cost."""
        if not self.can_spend(amount):
            raise RuntimeError("daily LLM budget exceeded")
        self.spent_usd += amount
