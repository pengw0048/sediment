"""Nightly mastery decay job.

For every active skill, recomputes ``unaided_retrievability`` as
``2 ** (-delta_hours / halflife)`` where ``halflife`` comes from the
:class:`pke.mastery.hlr.HLR` feature-based predictor evaluated against
the skill's mastery row. After the per-skill update the change is
spread one hop through the skill hierarchy with Anderson-style
activation: children pull mastery from their parents (alpha 0.7) and
parents soak a fraction of child mastery (alpha 0.4).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc
from pke.mastery.hlr import HLR, extract_features
from pke.mastery.spreading import spread_activation

_CHILD_TO_PARENT_ALPHA = 0.4
_PARENT_TO_CHILD_ALPHA = 0.7


def run(sqlite: SQLiteStore) -> int:
    """Recompute retrievability for every active skill. Returns count updated."""
    hlr = HLR()
    rows = sqlite.conn.execute(
        """
        SELECT s.id, s.last_seen_at,
               m.unaided_retrievability, m.unaided_reps,
               m.unaided_stability, m.unaided_difficulty,
               m.unaided_last_review_at,
               m.functional_reps, m.functional_stability, m.functional_difficulty
        FROM skill_nodes s
        JOIN skill_mastery_state m ON m.skill_id = s.id
        WHERE s.user_status = 'active'
        """
    ).fetchall()
    if not rows:
        return 0
    now = datetime.now(tz=UTC)
    timestamp = iso_utc()
    updates: list[tuple[float, str, str]] = []
    new_values: dict[str, float] = {}
    for row in rows:
        features = extract_features(dict(row), dimension="unaided")
        delta_hours = _delta_hours_since(row, now)
        new_retrievability = hlr.recall_probability(delta_hours=delta_hours, features=features)
        new_retrievability = max(0.0, min(1.0, new_retrievability))
        new_values[str(row["id"])] = new_retrievability
        updates.append((new_retrievability, timestamp, str(row["id"])))

    sqlite.conn.executemany(
        "UPDATE skill_mastery_state "
        "SET unaided_retrievability = ?, updated_at = ? "
        "WHERE skill_id = ?",
        updates,
    )
    spread_activation(
        sqlite,
        new_values=new_values,
        child_to_parent_alpha=_CHILD_TO_PARENT_ALPHA,
        parent_to_child_alpha=_PARENT_TO_CHILD_ALPHA,
        updated_at=timestamp,
    )
    sqlite.conn.commit()
    return len(updates)


def _delta_hours_since(row: dict[str, object], now: datetime) -> float:
    raw = row["unaided_last_review_at"] or row["last_seen_at"]
    if not raw:
        return 24.0 * 7
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(UTC)
    return max(0.0, (now - parsed).total_seconds() / 3600.0)
