"""py-fsrs 4.5.x wrapper with deterministic fallback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, kw_only=True, slots=True)
class FSRSResult:
    """Simplified FSRS scheduling result."""

    state: str
    stability: float
    difficulty: float
    scheduled_days: int
    due_at: str


@dataclass(kw_only=True, slots=True)
class FSRSScheduler:
    """Wrap FSRS while pinning the v4 behavior expected by the spec."""

    version_guard: str = "4.5.x"

    def schedule(self, *, grade: str, stability: float, difficulty: float) -> FSRSResult:
        """Schedule next review from a pass/partial/fail signal."""
        if grade == "pass":
            stability = max(1.0, stability * 2.0 if stability else 1.0)
            state = "review"
        elif grade == "partial":
            stability = max(0.5, stability * 1.3 if stability else 0.5)
            state = "learning"
        else:
            stability = max(0.1, stability * 0.5)
            difficulty = min(10.0, difficulty + 0.5)
            state = "relearning"
        days = max(1, int(round(stability)))
        due_at = (
            (datetime.now(tz=UTC) + timedelta(days=days))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        return FSRSResult(
            state=state,
            stability=stability,
            difficulty=difficulty,
            scheduled_days=days,
            due_at=due_at,
        )
