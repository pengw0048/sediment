"""Hypothesis-based property tests for mastery monotonicity.

The invariant being exercised: under a sequence of strictly positive
mastery deltas (``pass`` grades on any grader), ``unaided_retrievability``
must never decrease step-over-step. The clamp at 1.0 means the sequence
can plateau but never reverse, which is the load-bearing user
guarantee for the spaced-repetition loop.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pke.evidence.models import iso_utc, new_ulid
from pke.mastery.state import MasteryUpdater


def _seed_skill(app, *, skill_id: str) -> None:
    now = iso_utc()
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding, cluster_size,
          first_seen_at, last_seen_at, created_at, updated_at, user_status
        )
        VALUES (?, ?, '', x'', 1, ?, ?, ?, ?, 'active')
        """,
        (skill_id, skill_id, now, now, now, now),
    )
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_mastery_state(skill_id, updated_at) VALUES (?, ?)
        """,
        (skill_id, now),
    )
    app.sqlite.conn.commit()


_PASS_COMBOS = [
    ("symbolic", "replay_self_try"),
    ("symbolic", "variant"),
    ("llm_judge", "socratic"),
    ("llm_judge", "variant"),
    ("llm_judge", "explain_back"),
    ("self_report", "self_report"),
]


@given(
    combos=st.lists(
        st.sampled_from(_PASS_COMBOS),
        min_size=1,
        max_size=8,
    )
)
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_consecutive_passes_never_decrease_unaided_mastery(app, combos) -> None:
    """A stream of pass grades produces monotone non-decreasing mastery."""
    skill_id = new_ulid()
    _seed_skill(app, skill_id=skill_id)
    updater = MasteryUpdater(sqlite=app.sqlite)

    prior = 0.0
    for grader_kind, item_type in combos:
        updater.update_review(
            skill_id=skill_id,
            grade="pass",
            grader_kind=grader_kind,
            item_type=item_type,
        )
        row = app.sqlite.conn.execute(
            "SELECT unaided_retrievability FROM skill_mastery_state WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        current = float(row["unaided_retrievability"] or 0.0)
        assert current + 1e-9 >= prior, (
            f"unaided_retrievability dropped after a pass: {prior} -> {current} "
            f"on ({grader_kind}, {item_type})"
        )
        assert current <= 1.0 + 1e-9
        prior = current


@given(
    grades=st.lists(
        st.sampled_from(["fail"]),
        min_size=1,
        max_size=6,
    )
)
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_consecutive_fails_never_increase_unaided_mastery(app, grades) -> None:
    """A stream of fail grades produces monotone non-increasing mastery."""
    skill_id = new_ulid()
    _seed_skill(app, skill_id=skill_id)
    # Lift starting mastery so the negative delta has room to land.
    app.sqlite.conn.execute(
        "UPDATE skill_mastery_state SET unaided_retrievability = 1.0 WHERE skill_id = ?",
        (skill_id,),
    )
    app.sqlite.conn.commit()
    updater = MasteryUpdater(sqlite=app.sqlite)

    prior = 1.0
    for grade in grades:
        updater.update_review(
            skill_id=skill_id,
            grade=grade,
            grader_kind="symbolic",
            item_type="variant",
        )
        row = app.sqlite.conn.execute(
            "SELECT unaided_retrievability FROM skill_mastery_state WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        current = float(row["unaided_retrievability"] or 0.0)
        assert (
            current - 1e-9 <= prior
        ), f"unaided_retrievability rose after a fail: {prior} -> {current}"
        assert current >= 0.0
        prior = current
