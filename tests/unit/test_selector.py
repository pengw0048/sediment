"""ItemSelector scoring tests."""

from __future__ import annotations

from pke.mastery.selector import ItemSelector


def _insert_skill(
    app,
    *,
    skill_id: str,
    unaided_retrievability: float,
    unaided_reps: int = 0,
    outsource_count_7d: int = 0,
    days_since_review: int = 1,
) -> None:
    """Insert a skill plus mastery state row tuned for selector tests."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    last_review = (
        (datetime.now(tz=UTC) - timedelta(days=days_since_review))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding, first_seen_at, last_seen_at,
          created_at, updated_at
        )
        VALUES (?, ?, '', zeroblob(3072), ?, ?, ?, ?)
        """,
        (skill_id, skill_id, now, now, now, now),
    )
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_mastery_state(
          skill_id, unaided_retrievability, unaided_reps,
          unaided_last_review_at, outsource_count_7d, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (skill_id, unaided_retrievability, unaided_reps, last_review, outsource_count_7d, now),
    )
    app.sqlite.conn.commit()


def test_selector_prefers_low_unaided_high_outsource_skill(app):
    """A struggling skill with heavy AI assist should outrank a confident one."""
    _insert_skill(app, skill_id="weak", unaided_retrievability=0.2, outsource_count_7d=8)
    _insert_skill(app, skill_id="strong", unaided_retrievability=0.95, outsource_count_7d=0)
    scores = ItemSelector(sqlite=app.sqlite).select(limit=2)
    assert [s.skill_id for s in scores] == ["weak", "strong"]
    assert scores[0].score > scores[1].score


def test_selector_novelty_breaks_ties_for_unreviewed_skills(app):
    """Among similar skills, never-reviewed ones get the novelty boost."""
    _insert_skill(
        app, skill_id="fresh", unaided_retrievability=0.5, unaided_reps=0, days_since_review=10
    )
    _insert_skill(
        app, skill_id="stale", unaided_retrievability=0.5, unaided_reps=20, days_since_review=10
    )
    scores = ItemSelector(sqlite=app.sqlite).select(limit=2)
    fresh = next(s for s in scores if s.skill_id == "fresh")
    stale = next(s for s in scores if s.skill_id == "stale")
    assert fresh.score > stale.score


def test_selector_limit_caps_returned_candidates(app):
    """The selector returns at most ``limit`` candidates."""
    for i in range(7):
        _insert_skill(app, skill_id=f"s{i}", unaided_retrievability=0.5)
    scores = ItemSelector(sqlite=app.sqlite).select(limit=3)
    assert len(scores) == 3
