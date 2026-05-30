"""Review item selection scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from pke.db.sqlite import SQLiteStore


def sigmoid(value: float) -> float:
    """Return logistic sigmoid."""
    return 1.0 / (1.0 + math.exp(-value))


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

    def select(self, *, limit: int = 5) -> list[CandidateScore]:
        """Return top candidates for today's review."""
        rows = self.sqlite.conn.execute(
            """
            SELECT s.id, s.last_seen_at, m.unaided_retrievability, m.outsource_count_7d
            FROM skill_nodes s
            JOIN skill_mastery_state m ON m.skill_id = s.id
            WHERE s.user_status = 'active'
            """
        ).fetchall()
        now = datetime.now(tz=UTC)
        scored: list[CandidateScore] = []
        for row in rows:
            last_seen = datetime.fromisoformat(str(row["last_seen_at"]).replace("Z", "+00:00"))
            days = max(0.0, (now - last_seen).total_seconds() / 86400)
            recency = math.exp(-days / 7.0)
            unaided = float(row["unaided_retrievability"])
            gap = (1.0 - unaided) ** 1.5
            forgetting = sigmoid((0.85 - unaided) / 0.1)
            pressure = 1.0 + min(5.0, float(row["outsource_count_7d"]))
            score = recency * gap * forgetting * pressure
            scored.append(
                CandidateScore(
                    skill_id=str(row["id"]),
                    score=score,
                    reason=f"recency={recency:.2f} gap={gap:.2f} forgetting={forgetting:.2f}",
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
