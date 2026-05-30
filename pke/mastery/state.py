"""2D mastery state updates."""

from __future__ import annotations

from dataclasses import dataclass, field

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc
from pke.mastery.fsrs import FSRSScheduler
from pke.mastery.hlr import HLR

DELTA_TABLE = {
    ("pass", "symbolic", "replay_self_try"): 0.12,
    ("pass", "symbolic", "variant"): 0.10,
    ("pass", "llm_judge", "socratic"): 0.06,
    ("pass", "llm_judge", "variant"): 0.07,
    ("pass", "llm_judge", "explain_back"): 0.05,
    ("pass", "self_report", "any"): 0.04,
    ("partial", "symbolic", "any"): 0.04,
    ("partial", "llm_judge", "any"): 0.02,
    ("partial", "self_report", "any"): 0.01,
    ("fail", "symbolic", "any"): -0.08,
    ("fail", "llm_judge", "any"): -0.05,
    ("fail", "self_report", "any"): -0.02,
    ("skipped", "any", "any"): -0.01,
}


def grade_from_rating(rating: int) -> str:
    """Map FSRS 1-4 rating to grade text."""
    if rating >= 4:
        return "pass"
    if rating >= 2:
        return "partial"
    return "fail"


def delta_for(*, grade: str, grader_kind: str, item_type: str) -> float:
    """Return mastery delta from the frozen polarity table."""
    keys = [
        (grade, grader_kind, item_type),
        (grade, grader_kind, "any"),
        (grade, "any", "any"),
    ]
    for key in keys:
        if key in DELTA_TABLE:
            return DELTA_TABLE[key]
    return 0.0


@dataclass(kw_only=True, slots=True)
class MasteryUpdater:
    """Update independent unaided and functional mastery dimensions."""

    sqlite: SQLiteStore
    hlr: HLR = field(default_factory=HLR)
    fsrs: FSRSScheduler = field(default_factory=FSRSScheduler)

    def update_review(
        self,
        *,
        skill_id: str,
        grade: str,
        grader_kind: str,
        item_type: str,
        functional: bool = False,
    ) -> None:
        """Apply one review answer to mastery state."""
        row = self.sqlite.conn.execute(
            "SELECT * FROM skill_mastery_state WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if row is None:
            self.sqlite.execute(
                "INSERT INTO skill_mastery_state(skill_id, updated_at) VALUES (?, ?)",
                (skill_id, iso_utc()),
            )
            row = self.sqlite.conn.execute(
                "SELECT * FROM skill_mastery_state WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()
        dimension = "functional" if functional else "unaided"
        delta = delta_for(grade=grade, grader_kind=grader_kind, item_type=item_type)
        if functional:
            delta /= 2
        retrievability_col = "unaided_retrievability" if not functional else None
        current = float(
            row[retrievability_col] if retrievability_col else row["functional_stability"] / 10
        )
        new_value = min(1.0, max(0.0, current + 0.15 * delta))
        stability_col = f"{dimension}_stability"
        difficulty_col = f"{dimension}_difficulty"
        halflife_col = f"{dimension}_halflife_h"
        reps_col = f"{dimension}_reps"
        fsrs = self.fsrs.schedule(
            grade=grade,
            stability=float(row[stability_col]),
            difficulty=float(row[difficulty_col]),
        )
        halflife = self.hlr.update_halflife(halflife_h=float(row[halflife_col]), grade=grade)
        if functional:
            self.sqlite.execute(
                f"""
                UPDATE skill_mastery_state
                SET functional_stability = ?, functional_difficulty = ?, functional_halflife_h = ?,
                    functional_state = ?, functional_reps = {reps_col} + 1,
                    functional_last_at = ?, updated_at = ?
                WHERE skill_id = ?
                """,
                (
                    fsrs.stability,
                    fsrs.difficulty,
                    halflife,
                    fsrs.state,
                    iso_utc(),
                    iso_utc(),
                    skill_id,
                ),
            )
        else:
            self.sqlite.execute(
                """
                UPDATE skill_mastery_state
                SET unaided_retrievability = ?, unaided_stability = ?, unaided_difficulty = ?,
                    unaided_halflife_h = ?, unaided_state = ?, unaided_reps = unaided_reps + 1,
                    unaided_last_review_at = ?, unaided_due_at = ?, updated_at = ?
                WHERE skill_id = ?
                """,
                (
                    new_value,
                    fsrs.stability,
                    fsrs.difficulty,
                    halflife,
                    fsrs.state,
                    iso_utc(),
                    fsrs.due_at,
                    iso_utc(),
                    skill_id,
                ),
            )
