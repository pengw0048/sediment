"""Review session persistence and CLI helpers."""

from __future__ import annotations

from dataclasses import dataclass

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc, new_ulid
from pke.mastery.calibration import log_calibration
from pke.mastery.state import MasteryUpdater, grade_from_rating
from pke.review.grader import GradeResult
from pke.review.item_gen import GeneratedItem


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewSession:
    """Persisted review session."""

    id: str
    client: str
    selected_count: int


def create_session(sqlite: SQLiteStore, *, client: str, selected_count: int) -> ReviewSession:
    """Create a review session row."""
    session = ReviewSession(id=new_ulid(), client=client, selected_count=selected_count)
    sqlite.execute(
        """
        INSERT INTO review_sessions(id, started_at, client, selected_count)
        VALUES (?, ?, ?, ?)
        """,
        (session.id, iso_utc(), client, selected_count),
    )
    return session


def add_item(
    sqlite: SQLiteStore,
    *,
    session_id: str,
    skill_id: str,
    item: GeneratedItem,
    position: int,
    origin_evidence_id: str | None = None,
) -> str:
    """Persist a generated review item."""
    item_id = new_ulid()
    sqlite.execute(
        """
        INSERT INTO review_items(
          id, session_id, skill_id, item_type, prompt, oracle, grader,
          origin_evidence_id, presented_at, position
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            session_id,
            skill_id,
            item.item_type.value,
            item.prompt,
            item.oracle,
            item.grader,
            origin_evidence_id,
            iso_utc(),
            position,
        ),
    )
    return item_id


def answer_item(
    sqlite: SQLiteStore,
    *,
    item_id: str,
    self_rating: int,
    user_answer: str,
    grade: GradeResult,
    elapsed_ms: int,
) -> str:
    """Persist an answer, calibration log, and mastery update."""
    answer_id = new_ulid()
    sqlite.execute(
        """
        INSERT INTO review_answers(
          id, item_id, self_rating, user_answer, grade, judge_reasoning, answered_at, elapsed_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            answer_id,
            item_id,
            self_rating,
            user_answer,
            grade.rating,
            grade.feedback,
            iso_utc(),
            elapsed_ms,
        ),
    )
    row = sqlite.conn.execute("SELECT * FROM review_items WHERE id = ?", (item_id,)).fetchone()
    if row is not None:
        text_grade = grade_from_rating(grade.rating)
        log_calibration(
            sqlite,
            skill_id=str(row["skill_id"]),
            answer_id=answer_id,
            self_rating=self_rating,
            grade=text_grade,
        )
        MasteryUpdater(sqlite=sqlite).update_review(
            skill_id=str(row["skill_id"]),
            grade=text_grade,
            grader_kind=str(row["grader"]).replace("-", "_"),
            item_type=str(row["item_type"]),
        )
    return answer_id


def finish_session(sqlite: SQLiteStore, *, session_id: str) -> None:
    """Mark a review session finished."""
    sqlite.execute(
        """
        UPDATE review_sessions
        SET finished_at = ?,
            completed_count = (
              SELECT count(*)
              FROM review_items i
              JOIN review_answers a ON a.item_id = i.id
              WHERE i.session_id = review_sessions.id
            )
        WHERE id = ?
        """,
        (iso_utc(), session_id),
    )


def start_cli_review(*, limit: int = 5) -> str:
    """Return a simple CLI review text."""
    return f"Review session ready: {limit} items. Use the web or TUI for interactive answers."
