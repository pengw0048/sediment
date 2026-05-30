"""Calibration tracking."""

from __future__ import annotations

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc, new_ulid


def rating_to_probability(rating: int) -> float:
    """Map 1-4 self-rating to [0, 1]."""
    return {1: 0.0, 2: 0.33, 3: 0.66, 4: 1.0}[rating]


def grade_to_probability(grade: str) -> float:
    """Map grade text to [0, 1]."""
    return {"pass": 1.0, "partial": 0.5, "fail": 0.0}.get(grade, 0.5)


def log_calibration(
    sqlite: SQLiteStore,
    *,
    skill_id: str,
    answer_id: str,
    self_rating: int,
    grade: str,
) -> None:
    """Persist one calibration observation."""
    predicted = rating_to_probability(self_rating)
    actual = grade_to_probability(grade)
    sqlite.execute(
        """
        INSERT INTO calibration_log(id, skill_id, answer_id, predicted, actual, brier, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (new_ulid(), skill_id, answer_id, predicted, actual, (predicted - actual) ** 2, iso_utc()),
    )
