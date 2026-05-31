"""Resolve a free-text draft prompt to the best-matching existing skill node.

Used by the pre-Send Socratic intervention endpoint to translate the user's
draft text into a concrete ``skill_id`` so the gate chain can read the real
``unaided_mastery`` from ``skill_mastery_state`` and the per-skill
``gentle_every_n`` cooldown actually fires. Without this step the endpoint
ran with ``skill_label='unknown'`` and the per-skill throttle was a no-op.

The resolver embeds the draft once via the shared :class:`Embedder` (the
in-memory hash fallback is the hot path in tests and on machines without
sentence-transformers) and brute-forces a cosine search across the rows of
``skill_nodes``. For small graphs (hundreds of skills) this comfortably
clears the ~200 ms latency budget without paying hnswlib's init cost on
every request. If the catalog grows large enough that the linear scan
becomes a concern, swap in a cached :class:`AnnIndex`; the
:func:`resolve_prompt_to_skill` contract returns the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from pke.db.sqlite import SQLiteStore
from pke.identity.embedder import Embedder, cosine
from pke.identity.resolver import blob_to_vector

DEFAULT_SIMILARITY_THRESHOLD = 0.6


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillMatch:
    """One resolved skill node match for a draft prompt."""

    skill_id: str
    skill_label: str
    similarity: float
    unaided_mastery: float


def resolve_prompt_to_skill(
    *,
    sqlite: SQLiteStore,
    embedder: Embedder,
    prompt_text: str,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> SkillMatch | None:
    """Return the best-matching active skill node, or None below the threshold.

    Joins ``skill_nodes`` to ``skill_mastery_state`` so the caller gets
    the real ``unaided_retrievability`` for the gate chain. Skills with
    ``user_status='dropped'`` are filtered out — the user has explicitly
    told us they no longer want interventions for them. Skills whose
    embedding blob is empty (legacy rows or migrations in flight) are
    skipped silently.
    """
    if not prompt_text.strip():
        return None
    rows = sqlite.conn.execute(
        """
        SELECT n.id, n.canonical_name, n.embedding,
               COALESCE(m.unaided_retrievability, 0.5) AS unaided_retrievability
        FROM skill_nodes AS n
        LEFT JOIN skill_mastery_state AS m ON m.skill_id = n.id
        WHERE n.user_status != 'dropped'
        """
    ).fetchall()
    if not rows:
        return None
    query_vector = embedder.embed(prompt_text)
    best: SkillMatch | None = None
    for row in rows:
        vector = blob_to_vector(row["embedding"])
        if not vector or len(vector) != len(query_vector):
            continue
        similarity = cosine(query_vector, vector)
        if best is None or similarity > best.similarity:
            best = SkillMatch(
                skill_id=str(row["id"]),
                skill_label=str(row["canonical_name"]),
                similarity=similarity,
                unaided_mastery=float(row["unaided_retrievability"]),
            )
    if best is None or best.similarity < threshold:
        return None
    return best


__all__ = ["DEFAULT_SIMILARITY_THRESHOLD", "SkillMatch", "resolve_prompt_to_skill"]
