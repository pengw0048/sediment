"""Shared session helpers used by both the TUI and the HTTP API.

Keeps the start-session / answer-item / fetch-today flows in one place so
either entry point produces the same database state. Pure Python with no
Textual or FastAPI imports; both layers wrap it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pke.mastery.selector import CandidateScore, ItemSelector
from pke.mastery.state import grade_from_rating
from pke.review.grader import Grader, GradeResult
from pke.review.item_gen import ItemGenerator
from pke.review.session import add_item, answer_item, create_session


@dataclass(frozen=True, kw_only=True, slots=True)
class TodayRow:
    """One row of the today view: a scored, named candidate."""

    skill_id: str
    name: str
    score: float
    unaided: float
    reps: int
    outsource_7d: int
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class PreparedItem:
    """An item generated and persisted, with its prompt/grader/oracle attached."""

    item_id: str
    skill_id: str
    skill_name: str
    item_type: str
    prompt: str
    oracle: str | None
    grader: str


def fetch_today(app: Any, *, limit: int = 5) -> list[TodayRow]:
    """Return today's top-K candidates as display-ready rows."""
    selector = ItemSelector(sqlite=app.sqlite)
    scored: list[CandidateScore] = selector.select(limit=limit)
    if not scored:
        return []
    rows = app.sqlite.conn.execute(
        f"""
        SELECT s.id, s.canonical_name,
               m.unaided_retrievability, m.unaided_reps, m.outsource_count_7d
        FROM skill_nodes s
        JOIN skill_mastery_state m ON m.skill_id = s.id
        WHERE s.id IN ({",".join("?" * len(scored))})
        """,
        tuple(c.skill_id for c in scored),
    ).fetchall()
    by_id = {str(row["id"]): row for row in rows}
    out: list[TodayRow] = []
    for candidate in scored:
        row = by_id.get(candidate.skill_id)
        if row is None:
            continue
        out.append(
            TodayRow(
                skill_id=candidate.skill_id,
                name=str(row["canonical_name"]),
                score=candidate.score,
                unaided=float(row["unaided_retrievability"] or 0.0),
                reps=int(row["unaided_reps"] or 0),
                outsource_7d=int(row["outsource_count_7d"] or 0),
                reason=candidate.reason,
            )
        )
    return out


async def start_session(app: Any, *, limit: int = 5, client: str = "tui") -> list[PreparedItem]:
    """Run select → generate → add_item for one full session and return the items."""
    today = fetch_today(app, limit=limit)
    if not today:
        return []
    session = create_session(app.sqlite, client=client, selected_count=len(today))
    generator = ItemGenerator(client=getattr(app, "llm_client", None))
    prepared: list[PreparedItem] = []
    for position, candidate in enumerate(today):
        item = await generator.generate(
            skill_label=candidate.name,
            evidence_text="",
            unaided_mastery=candidate.unaided,
            evidence_count=candidate.reps + 1,
            recent_outsource_count=candidate.outsource_7d,
        )
        item_id = add_item(
            app.sqlite,
            session_id=session.id,
            skill_id=candidate.skill_id,
            item=item,
            position=position,
        )
        prepared.append(
            PreparedItem(
                item_id=item_id,
                skill_id=candidate.skill_id,
                skill_name=candidate.name,
                item_type=item.item_type.value,
                prompt=item.prompt,
                oracle=item.oracle,
                grader=item.grader,
            )
        )
    return prepared


async def grade_answer(
    app: Any,
    *,
    item: PreparedItem,
    user_answer: str,
    self_rating: int,
    elapsed_ms: int,
) -> GradeResult:
    """Grade ``user_answer`` against ``item`` and persist a review_answers row."""
    grader = Grader(client=getattr(app, "llm_client", None))
    grade: GradeResult
    if item.grader == "regex":
        grade = grader.grade_regex(answer=user_answer, pattern=item.oracle or "")
    elif item.grader == "code_exec":
        grade = grader.grade_code(answer=user_answer, test_code=item.oracle or "")
    elif item.grader == "self_report":
        grade = grader.grade_self_report(
            grade=_grade_word(self_rating),
            reason="",
        )
    elif item.grader == "llm_judge":
        rubric = Grader.parse_rubric(item.oracle if isinstance(item.oracle, str) else None)
        try:
            grade = await grader.grade_llm_judge(
                item_prompt=item.prompt,
                user_answer=user_answer,
                rubric=rubric,
            )
        except RuntimeError:
            grade = grader.grade_self_report(
                grade=_grade_word(self_rating),
                reason="no llm client; recorded as self-report",
            )
    else:
        grade = grader.grade_self_report(grade="partial", reason=f"unknown grader {item.grader}")
    answer_item(
        app.sqlite,
        item_id=item.item_id,
        self_rating=self_rating,
        user_answer=user_answer,
        grade=grade,
        elapsed_ms=elapsed_ms,
    )
    return grade


def grade_word(rating: int) -> str:
    """Map a 1-4 self rating to the symbolic grade vocabulary."""
    return _grade_word(rating)


def _grade_word(rating: int) -> str:
    if rating <= 1:
        return "fail"
    if rating == 2:
        return "partial"
    if rating == 3:
        return "partial"
    return "pass"


def label_from_rating(rating: int) -> str:
    """User-visible label for the four-step self-rating scale."""
    return grade_from_rating(rating)
