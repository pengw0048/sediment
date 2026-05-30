"""Skill identity resolver."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc, new_ulid
from pke.identity.ann_index import AnnIndex
from pke.identity.embedder import Embedder


def vector_to_blob(vector: list[float]) -> bytes:
    """Pack float32 vector into SQLite BLOB."""
    return struct.pack(f"{len(vector)}f", *vector)


def blob_to_vector(blob: bytes) -> list[float]:
    """Unpack a float32 SQLite BLOB."""
    if not blob:
        return []
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolveDecision:
    """Identity resolution result."""

    candidate_id: str
    skill_id: str
    action: str
    similarity: float
    llm_judge_triggered: bool


@dataclass(kw_only=True, slots=True)
class IdentityResolver:
    """Resolve extracted candidates into canonical skill nodes."""

    sqlite: SQLiteStore
    embedder: Embedder
    ann: AnnIndex
    merge_threshold: float = 0.86
    gray_lower: float = 0.78
    gray_upper: float = 0.92

    def resolve_pending(self, *, limit: int = 100) -> list[ResolveDecision]:
        """Resolve pending skill candidates."""
        rows = self.sqlite.conn.execute(
            """
            SELECT * FROM skill_candidates
            WHERE resolution_state = 'pending'
            ORDER BY created_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        decisions: list[ResolveDecision] = []
        for row in rows:
            vector = self.embedder.embed(str(row["normalized_name"]))
            nearest = self.ann.search(vector, k=1)
            if nearest and nearest[0][1] >= self.merge_threshold:
                skill_id = nearest[0][0]
                action = "merge"
                similarity = nearest[0][1]
            else:
                skill_id = self._create_skill(
                    name=str(row["normalized_name"]),
                    description=str(row["description"] or ""),
                    vector=vector,
                )
                action = "new"
                similarity = nearest[0][1] if nearest else 0.0
            llm_judge = self.gray_lower <= similarity <= self.gray_upper
            self.sqlite.conn.execute(
                """
                UPDATE skill_candidates
                SET resolved_skill_id = ?, resolution_state = 'auto', embedding = ?
                WHERE id = ?
                """,
                (skill_id, vector_to_blob(vector), row["id"]),
            )
            self.sqlite.conn.execute(
                """
                INSERT OR IGNORE INTO skill_evidence_link(
                  skill_id, evidence_id, candidate_id, evidence_kind, occurred_at
                )
                SELECT ?, evidence_id, id, evidence_kind, created_at
                FROM skill_candidates WHERE id = ?
                """,
                (skill_id, row["id"]),
            )
            decisions.append(
                ResolveDecision(
                    candidate_id=str(row["id"]),
                    skill_id=skill_id,
                    action=action,
                    similarity=similarity,
                    llm_judge_triggered=llm_judge,
                )
            )
        self.sqlite.conn.commit()
        return decisions

    def _create_skill(self, *, name: str, description: str, vector: list[float]) -> str:
        now = iso_utc()
        skill_id = new_ulid()
        self.sqlite.conn.execute(
            """
            INSERT INTO skill_nodes(
              id, canonical_name, description, embedding, cluster_size,
              first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (skill_id, name, description, vector_to_blob(vector), now, now, now, now),
        )
        self.sqlite.conn.execute(
            """
            INSERT OR IGNORE INTO skill_mastery_state(skill_id, updated_at)
            VALUES (?, ?)
            """,
            (skill_id, now),
        )
        self.ann.add(skill_id, vector)
        return skill_id

    def decision_log_payload(self, decisions: list[ResolveDecision]) -> str:
        """Return stable JSON for debug output."""
        return json.dumps([decision.__dict__ for decision in decisions], sort_keys=True)
