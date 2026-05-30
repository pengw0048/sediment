"""TUI ↔ web parity tests for the session helpers.

These exercise the in-process helpers the TUI calls (``fetch_today``,
``start_session``, ``grade_answer``) without spinning up Textual or
FastAPI, so the two front-ends cannot drift apart in the database
state they produce.
"""

from __future__ import annotations

import pytest

from pke.evidence.models import iso_utc, new_ulid
from pke.testing import MockLLMClient
from pke.tui.service import fetch_today, grade_answer, start_session

pytestmark = pytest.mark.asyncio


def _seed_skill(app, *, name: str, unaided: float, reps: int, outsource: int = 0) -> str:
    skill_id = new_ulid()
    now = iso_utc()
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding, cluster_size,
          first_seen_at, last_seen_at, created_at, updated_at, user_status
        )
        VALUES (?, ?, '', x'', 1, ?, ?, ?, ?, 'active')
        """,
        (skill_id, name, now, now, now, now),
    )
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_mastery_state(
          skill_id, unaided_retrievability, unaided_reps,
          outsource_count_7d, updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (skill_id, unaided, reps, outsource, now),
    )
    app.sqlite.conn.commit()
    return skill_id


async def test_fetch_today_ranks_by_selector(app) -> None:
    """fetch_today returns rows whose ordering matches ItemSelector's scoring."""
    a = _seed_skill(app, name="forgotten", unaided=0.10, reps=3, outsource=8)
    b = _seed_skill(app, name="fresh", unaided=0.90, reps=20, outsource=0)

    rows = fetch_today(app, limit=10)

    ids = [row.skill_id for row in rows]
    assert ids[0] == a, "low-mastery + heavy outsource skill should win"
    assert b in ids


async def test_start_session_persists_items(app) -> None:
    """start_session inserts one review_items row per selected skill."""
    app.llm_client = MockLLMClient()
    _seed_skill(app, name="kubectl describe", unaided=0.30, reps=2, outsource=3)
    _seed_skill(app, name="fastapi routes", unaided=0.40, reps=1, outsource=2)

    prepared = await start_session(app, limit=2, client="tui")

    assert len(prepared) == 2
    row_count = app.sqlite.conn.execute("SELECT COUNT(*) AS c FROM review_items").fetchone()["c"]
    assert row_count == 2
    session_count = app.sqlite.conn.execute(
        "SELECT COUNT(*) AS c FROM review_sessions WHERE client = 'tui'"
    ).fetchone()["c"]
    assert session_count == 1
    for item in prepared:
        assert item.prompt
        assert item.grader


async def test_grade_answer_writes_review_answer_and_returns_grade(app) -> None:
    """grade_answer persists exactly one review_answers row and returns a grade."""
    app.llm_client = MockLLMClient()
    _seed_skill(app, name="vector indexing", unaided=0.20, reps=0, outsource=1)

    prepared = await start_session(app, limit=1, client="tui")
    assert prepared, "expected at least one item from seeded skill"
    item = prepared[0]

    grade = await grade_answer(
        app,
        item=item,
        user_answer="i would build an hnsw index with M=32 and ef_construction=200",
        self_rating=3,
        elapsed_ms=1234,
    )

    assert grade.grade in {"pass", "partial", "fail"}
    answer_count = app.sqlite.conn.execute(
        "SELECT COUNT(*) AS c FROM review_answers WHERE item_id = ?",
        (item.item_id,),
    ).fetchone()["c"]
    assert answer_count == 1
