"""Review item selection scoring.

The selector ranks active skills by their need-for-review and returns the
top-K. A higher score means "this skill should land in the next review
session." The formula is an additive weighted sum so each component is
inspectable:

    score = w_forgetting * (1 - p_recall)
          + w_mastery_gap * (1 - unaided_mastery)
          + w_outsource * outsource_recency
          + w_novelty * novelty

Each component lives in ``[0, 1]``; the weights configure their relative
priority and sum to 1.0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pke.db.sqlite import SQLiteStore
from pke.mastery.hlr import HLR, extract_features


@dataclass(frozen=True, kw_only=True, slots=True)
class CandidateScore:
    """Scored review candidate."""

    skill_id: str
    score: float
    reason: str


@dataclass(kw_only=True, slots=True)
class ItemSelector:
    """Select due review items from mastery and evidence state."""

    sqlite: SQLiteStore
    hlr: HLR = field(default_factory=HLR)
    # Weighted-sum coefficients. The defaults emphasize forgetting and the
    # gap to mastery, with secondary weight on AI-assist pressure and a
    # small novelty term to keep recently surfaced skills from monopolizing
    # the queue.
    w_forgetting: float = 0.40
    w_mastery_gap: float = 0.30
    w_outsource: float = 0.20
    w_novelty: float = 0.10
    # Cap on how heavily a single skill's outsource burst can boost its score.
    outsource_cap: int = 10

    def select_one(self, skill_id: str) -> CandidateScore | None:
        """Return a manual-review candidate for a single skill.

        Used by the per-skill "Review now" button on the Skills page: the
        caller already knows exactly which skill to review, so the
        weighted-sum ranking is irrelevant. The selector still owns the
        liveness check — only skills with both an active ``skill_nodes``
        row and a ``skill_mastery_state`` row are eligible — which keeps
        callers from inventing review sessions against archived or
        never-mastered skills.

        Returns ``None`` when the skill does not exist or is not active.
        """
        row = self.sqlite.conn.execute(
            """
            SELECT s.id
            FROM skill_nodes s
            JOIN skill_mastery_state m ON m.skill_id = s.id
            WHERE s.id = ? AND s.user_status = 'active'
            """,
            (skill_id,),
        ).fetchone()
        if row is None:
            return None
        return CandidateScore(
            skill_id=str(row["id"]),
            score=0.0,
            reason="manual review-now",
        )

    def select(self, *, limit: int = 5, user_id: str | None = None) -> list[CandidateScore]:
        """Return top candidates for today's review."""
        del user_id  # user_id-scoped queries land in a multi-user iteration
        rows = self.sqlite.conn.execute(
            """
            SELECT s.id, s.last_seen_at,
                   m.unaided_retrievability, m.unaided_reps,
                   m.unaided_stability, m.unaided_difficulty,
                   m.functional_reps, m.functional_stability, m.functional_difficulty,
                   m.unaided_last_review_at, m.outsource_count_7d
            FROM skill_nodes s
            JOIN skill_mastery_state m ON m.skill_id = s.id
            WHERE s.user_status = 'active'
            """
        ).fetchall()
        now = datetime.now(tz=UTC)
        scored: list[CandidateScore] = []
        for row in rows:
            features = extract_features(dict(row), dimension="unaided")
            delta_hours = _delta_hours_since(row, now)
            p_recall = self.hlr.recall_probability(delta_hours=delta_hours, features=features)
            forgetting_term = 1.0 - p_recall
            unaided = float(row["unaided_retrievability"] or 0.0)
            mastery_gap = max(0.0, 1.0 - unaided)
            outsource_count = float(row["outsource_count_7d"] or 0)
            outsource_term = min(1.0, outsource_count / max(1, self.outsource_cap))
            reps = float(row["unaided_reps"] or 0)
            novelty_term = 1.0 / (1.0 + reps)
            score = (
                self.w_forgetting * forgetting_term
                + self.w_mastery_gap * mastery_gap
                + self.w_outsource * outsource_term
                + self.w_novelty * novelty_term
            )
            scored.append(
                CandidateScore(
                    skill_id=str(row["id"]),
                    score=score,
                    reason=(
                        f"forgetting={forgetting_term:.2f} "
                        f"gap={mastery_gap:.2f} "
                        f"outsource={outsource_term:.2f} "
                        f"novelty={novelty_term:.2f}"
                    ),
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


def _delta_hours_since(row: dict[str, object], now: datetime) -> float:
    """Hours since the skill's last unaided review, falling back to last_seen_at."""
    raw = row["unaided_last_review_at"] or row["last_seen_at"]
    if not raw:
        # Never reviewed: treat as overdue so a cold skill is not eternally hidden.
        return 24.0 * 7
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(UTC)
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def sigmoid(value: float) -> float:
    """Return logistic sigmoid. Kept for callers outside this module."""
    return 1.0 / (1.0 + math.exp(-value))
