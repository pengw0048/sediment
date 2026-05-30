"""Tests for the nightly decay job and Anderson spreading-activation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pke.maintenance.jobs import decay
from pke.mastery.spreading import spread_activation


def _stamp_at(days_ago: float) -> str:
    moment = datetime.now(tz=UTC) - timedelta(days=days_ago)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_skill(
    app, *, skill_id: str, last_review_days_ago: float, retrievability: float
) -> None:
    """Insert a skill plus mastery state for a decay test."""
    now = _stamp_at(0)
    last_review = _stamp_at(last_review_days_ago)
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding, first_seen_at, last_seen_at,
          created_at, updated_at
        )
        VALUES (?, ?, '', zeroblob(3072), ?, ?, ?, ?)
        """,
        (skill_id, skill_id, last_review, now, now, now),
    )
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_mastery_state(
          skill_id, unaided_retrievability, unaided_reps,
          unaided_last_review_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (skill_id, retrievability, 0, last_review, now),
    )
    app.sqlite.conn.commit()


def test_decay_lowers_retrievability_for_stale_skill(app):
    """A skill reviewed long ago decays below its starting retrievability."""
    _insert_skill(app, skill_id="stale", last_review_days_ago=60.0, retrievability=0.95)
    updated = decay.run(app.sqlite)
    assert updated == 1
    after = app.sqlite.conn.execute(
        "SELECT unaided_retrievability FROM skill_mastery_state WHERE skill_id = ?",
        ("stale",),
    ).fetchone()
    assert after["unaided_retrievability"] < 0.95


def test_decay_keeps_fresh_skill_high(app):
    """A skill reviewed minutes ago should still be near 1.0 after decay."""
    _insert_skill(app, skill_id="fresh", last_review_days_ago=0.01, retrievability=0.5)
    decay.run(app.sqlite)
    after = app.sqlite.conn.execute(
        "SELECT unaided_retrievability FROM skill_mastery_state WHERE skill_id = ?",
        ("fresh",),
    ).fetchone()
    assert after["unaided_retrievability"] > 0.9


def test_spread_activation_blends_parent_and_child(app):
    """Spreading pulls a parent up toward a confident child and vice versa."""
    _insert_skill(app, skill_id="parent", last_review_days_ago=10, retrievability=0.4)
    _insert_skill(app, skill_id="child", last_review_days_ago=10, retrievability=0.9)
    new_values = {"parent": 0.4, "child": 0.9}
    spread_activation(
        app.sqlite,
        new_values=new_values,
        child_to_parent_alpha=0.4,
        parent_to_child_alpha=0.7,
        updated_at=_stamp_at(0),
        edges=[("parent", "child")],
    )
    # parent + 0.4 * (0.9 - 0.4) = 0.6
    assert abs(new_values["parent"] - 0.6) < 1e-9
    # child + 0.7 * (0.4 - 0.9) = 0.55
    assert abs(new_values["child"] - 0.55) < 1e-9
    persisted = {
        row["skill_id"]: row["unaided_retrievability"]
        for row in app.sqlite.conn.execute(
            "SELECT skill_id, unaided_retrievability FROM skill_mastery_state"
        ).fetchall()
    }
    assert abs(persisted["parent"] - 0.6) < 1e-9
    assert abs(persisted["child"] - 0.55) < 1e-9


def test_spread_activation_noop_without_edges(app):
    """No edges -> retrievability is left exactly as it came in."""
    _insert_skill(app, skill_id="solo", last_review_days_ago=1, retrievability=0.5)
    new_values = {"solo": 0.5}
    spread_activation(
        app.sqlite,
        new_values=new_values,
        child_to_parent_alpha=0.4,
        parent_to_child_alpha=0.7,
        updated_at=_stamp_at(0),
        edges=[],
    )
    assert new_values == {"solo": 0.5}
