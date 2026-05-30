"""FSRS scheduler wrapper backed by the upstream fsrs package."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fsrs import Card, Rating, Scheduler


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

    scheduler: Scheduler

    def __init__(self) -> None:
        self.scheduler = Scheduler(enable_fuzzing=False)

    def schedule(self, *, grade: str, stability: float, difficulty: float) -> FSRSResult:
        """Schedule next review from a pass/partial/fail signal using FSRS."""
        review_at = datetime.now(tz=UTC)
        has_fsrs_state = stability > 0 and difficulty > 0
        card = Card(
            stability=stability if has_fsrs_state else None,
            difficulty=difficulty if has_fsrs_state else None,
            due=review_at,
            last_review=review_at if has_fsrs_state else None,
        )
        reviewed = self.scheduler.review_card(card, self._rating_for_grade(grade), review_at)[0]
        due_at = reviewed.due.astimezone(UTC)
        scheduled_days = max(0, int(round((due_at - review_at).total_seconds() / 86_400)))
        return FSRSResult(
            state=str(reviewed.state.name).lower(),
            stability=float(reviewed.stability or 0.0),
            difficulty=float(reviewed.difficulty or 0.0),
            scheduled_days=scheduled_days,
            due_at=due_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        )

    @staticmethod
    def _rating_for_grade(grade: str) -> Rating:
        if grade == "pass":
            return Rating.Good
        if grade == "partial":
            return Rating.Hard
        return Rating.Again
