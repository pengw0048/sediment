"""Skill identity resolver."""

from __future__ import annotations

import asyncio
import json
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Coroutine

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc, new_ulid
from pke.extraction.llm_client import LLMClient
from pke.extraction.prompts import render as render_prompt
from pke.extraction.schema import clamp01
from pke.identity.ann_index import AnnIndex
from pke.identity.audit_reasons import AuditReason
from pke.identity.denstream_online import OnlineClusterer
from pke.identity.embedder import Embedder


def vector_to_blob(vector: list[float]) -> bytes:
    """Pack float32 vector into SQLite BLOB."""
    return struct.pack(f"{len(vector)}f", *vector)


def blob_to_vector(blob: bytes) -> list[float]:
    """Unpack a float32 SQLite BLOB."""
    if not blob:
        return []
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _run_coroutine_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run ``coro`` to completion regardless of whether a loop is already active.

    ``IdentityResolver.resolve_pending`` is a synchronous entry point but its
    judge call is async. When invoked from a synchronous context — a cron
    job or the maintenance daemon — ``asyncio.run`` works directly. When
    invoked from inside an event loop (FastAPI request, pytest-asyncio
    auto-mode test), ``asyncio.run`` raises because nested loops are not
    allowed; in that case we ship the coroutine to a worker thread that
    owns its own loop. Either way the caller stays sync.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolveDecision:
    """Identity resolution result."""

    candidate_id: str
    skill_id: str
    action: str
    similarity: float
    llm_judge_triggered: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class GrayBandVerdict:
    """LLM judge verdict on a gray-band candidate pair."""

    verdict: str  # merge | new | pending
    confidence: float
    rationale: str


@dataclass(kw_only=True, slots=True)
class IdentityResolver:
    """Resolve extracted candidates into canonical skill nodes.

    Three regions on cosine similarity to an existing skill node:

    * ``>= merge_threshold`` (default 0.92) — auto-merge.
    * ``<= gray_lower`` (default 0.78) — auto-create a new skill node.
    * ``gray_lower < cos < merge_threshold`` — the LLM gray-band judge
      decides (``merge`` / ``new`` / ``pending``). When ``pending``, the
      candidate is queued in ``pending_audits`` for human review.

    A judge client is optional: without one, the resolver falls back to
    the legacy single-threshold behavior so the layer stays usable when
    no LLM is configured.

    Leaky-bucket guard: ``daily_new_skill_cap`` limits how many new
    skill nodes the resolver may auto-create per UTC day. Once the cap
    is reached, "new" decisions are demoted to ``pending`` and the
    blocked candidate is queued in ``pending_audits`` with a
    ``candidate_review`` audit type and ``reason='leaky_bucket_block'``
    in its payload. Without this guard a noisy extraction run could
    flood the skill graph with thousands of low-value nodes in a single
    day. Set to ``0`` to disable.
    """

    sqlite: SQLiteStore
    embedder: Embedder
    ann: AnnIndex
    judge_client: LLMClient | None = field(default=None)
    online_clusterer: OnlineClusterer | None = field(default=None)
    merge_threshold: float = 0.92
    gray_lower: float = 0.78
    legacy_merge_threshold: float = 0.86
    daily_new_skill_cap: int = 50

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
            decisions.append(self._resolve_one(row))
        self.sqlite.conn.commit()
        return decisions

    def _resolve_one(self, row: dict[str, object]) -> ResolveDecision:
        candidate_name = str(row["normalized_name"])
        candidate_desc = str(row["description"] or "")
        vector = self.embedder.embed(candidate_name)
        nearest = self.ann.search(vector, k=1)
        nearest_id = nearest[0][0] if nearest else None
        nearest_sim = nearest[0][1] if nearest else 0.0
        micro_cluster_id = (
            self.online_clusterer.partial_fit(vector) if self.online_clusterer is not None else None
        )

        action, skill_id, judge_triggered = self._decide(
            candidate_name=candidate_name,
            candidate_desc=candidate_desc,
            vector=vector,
            nearest_id=nearest_id,
            nearest_sim=nearest_sim,
            candidate_id=str(row["id"]),
        )

        resolution_state = "auto" if action != "pending" else "pending_audit"
        self.sqlite.conn.execute(
            """
            UPDATE skill_candidates
            SET resolved_skill_id = ?, resolution_state = ?, embedding = ?,
                micro_cluster_id = ?
            WHERE id = ?
            """,
            (skill_id, resolution_state, vector_to_blob(vector), micro_cluster_id, row["id"]),
        )
        if skill_id is not None and action != "pending":
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

        return ResolveDecision(
            candidate_id=str(row["id"]),
            skill_id=skill_id or "",
            action=action,
            similarity=nearest_sim,
            llm_judge_triggered=judge_triggered,
        )

    def _decide(  # noqa: PLR0911
        self,
        *,
        candidate_name: str,
        candidate_desc: str,
        vector: list[float],
        nearest_id: str | None,
        nearest_sim: float,
        candidate_id: str,
    ) -> tuple[str, str | None, bool]:
        if nearest_id is not None and nearest_sim >= self.merge_threshold:
            return ("merge", nearest_id, False)
        if nearest_id is None or nearest_sim <= self.gray_lower:
            return self._new_or_blocked(
                candidate_id=candidate_id,
                candidate_name=candidate_name,
                candidate_desc=candidate_desc,
                vector=vector,
                nearest_id=nearest_id,
                nearest_sim=nearest_sim,
                judge_triggered=False,
            )

        # Gray band: call the LLM judge if one is configured. Without a judge
        # we fall back to the legacy single threshold so the layer stays usable.
        if self.judge_client is None:
            if nearest_sim >= self.legacy_merge_threshold:
                return ("merge", nearest_id, False)
            return self._new_or_blocked(
                candidate_id=candidate_id,
                candidate_name=candidate_name,
                candidate_desc=candidate_desc,
                vector=vector,
                nearest_id=nearest_id,
                nearest_sim=nearest_sim,
                judge_triggered=False,
            )

        verdict = self._call_gray_band_judge(
            candidate_name=candidate_name,
            candidate_desc=candidate_desc,
            existing_skill_id=nearest_id,
            cosine=nearest_sim,
        )
        if verdict.verdict == "merge":
            return ("merge", nearest_id, True)
        if verdict.verdict == "pending":
            self._enqueue_audit(
                candidate_id=candidate_id,
                candidate_name=candidate_name,
                existing_skill_id=nearest_id,
                cosine=nearest_sim,
                verdict=verdict,
            )
            return ("pending", nearest_id, True)
        return self._new_or_blocked(
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            candidate_desc=candidate_desc,
            vector=vector,
            nearest_id=nearest_id,
            nearest_sim=nearest_sim,
            judge_triggered=True,
        )

    def _new_or_blocked(
        self,
        *,
        candidate_id: str,
        candidate_name: str,
        candidate_desc: str,
        vector: list[float],
        nearest_id: str | None,
        nearest_sim: float,
        judge_triggered: bool,
    ) -> tuple[str, str | None, bool]:
        """Try to create a new skill, or demote to ``pending`` if the daily cap is hit.

        When the leaky bucket is full the candidate is queued in
        ``pending_audits`` as a ``candidate_review`` row whose payload
        carries ``reason='leaky_bucket_block'`` so the audit surface can
        present these alongside (and distinguish them from) regular
        gray-band reviews. The candidate's ``resolved_skill_id`` stays
        unset and ``resolution_state`` flips to ``pending_audit`` in
        :meth:`_resolve_one`.
        """
        skill_id = self._create_skill(candidate_name, candidate_desc, vector)
        if skill_id is not None:
            return ("new", skill_id, judge_triggered)
        self._enqueue_leaky_bucket_audit(
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            candidate_desc=candidate_desc,
            nearest_id=nearest_id,
            nearest_sim=nearest_sim,
        )
        return ("pending", None, judge_triggered)

    def _call_gray_band_judge(
        self,
        *,
        candidate_name: str,
        candidate_desc: str,
        existing_skill_id: str,
        cosine: float,
    ) -> GrayBandVerdict:
        existing = self.sqlite.conn.execute(
            "SELECT canonical_name, description FROM skill_nodes WHERE id = ?",
            (existing_skill_id,),
        ).fetchone()
        existing_name = str(existing["canonical_name"]) if existing else ""
        existing_desc = str(existing["description"]) if existing else ""

        system = render_prompt("identity_gray_band.system.j2")
        user = render_prompt(
            "identity_gray_band.user.j2",
            cosine=cosine,
            candidate_name=candidate_name,
            candidate_description=candidate_desc or "(no description)",
            existing_name=existing_name,
            existing_description=existing_desc or "(no description)",
        )
        assert self.judge_client is not None
        from pke.extraction.llm_client import call_kind

        with call_kind("gray_band"):
            payload = _run_coroutine_sync(self.judge_client.complete_json(system=system, user=user))
        verdict = str(payload.get("verdict", "new")).lower()
        if verdict not in {"merge", "new", "pending"}:
            verdict = "new"
        try:
            confidence = clamp01(float(payload.get("confidence", 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        return GrayBandVerdict(
            verdict=verdict,
            confidence=confidence,
            rationale=str(payload.get("rationale", "")),
        )

    def _enqueue_audit(
        self,
        *,
        candidate_id: str,
        candidate_name: str,
        existing_skill_id: str,
        cosine: float,
        verdict: GrayBandVerdict,
    ) -> None:
        self.sqlite.conn.execute(
            """
            INSERT INTO pending_audits(id, audit_type, payload_json, created_at)
            VALUES (?, 'candidate_review', ?, ?)
            """,
            (
                new_ulid(),
                json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "candidate_name": candidate_name,
                        "existing_skill_id": existing_skill_id,
                        "cosine": cosine,
                        "judge": {
                            "verdict": verdict.verdict,
                            "confidence": verdict.confidence,
                            "rationale": verdict.rationale,
                        },
                    }
                ),
                iso_utc(),
            ),
        )

    def _create_skill(self, name: str, description: str, vector: list[float]) -> str | None:
        """Insert a new skill row, unless the daily leaky-bucket cap is full.

        Returns the new skill id on success, or ``None`` when the cap
        has been reached; in that case the caller enqueues a
        ``leaky_bucket_block`` audit and demotes the candidate to
        ``pending``.
        """
        if self.daily_new_skill_cap > 0:
            row = self.sqlite.conn.execute(
                "SELECT count(*) AS n FROM skill_nodes "
                "WHERE date(created_at) = date('now')"
            ).fetchone()
            created_today = int(row["n"]) if row is not None else 0
            if created_today >= self.daily_new_skill_cap:
                return None
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

    def _enqueue_leaky_bucket_audit(
        self,
        *,
        candidate_id: str,
        candidate_name: str,
        candidate_desc: str,
        nearest_id: str | None,
        nearest_sim: float,
    ) -> None:
        """Queue a ``candidate_review`` row tagged ``leaky_bucket_block``.

        We reuse the existing ``candidate_review`` audit type rather
        than introducing a new value because ``pending_audits.audit_type``
        is CHECK-constrained in migration 0001 to a closed set
        (``split``, ``merge``, ``candidate_review``). The blocked-by-cap
        signal lives in the payload's ``reason`` field instead.
        """
        self.sqlite.conn.execute(
            """
            INSERT INTO pending_audits(id, audit_type, payload_json, created_at)
            VALUES (?, 'candidate_review', ?, ?)
            """,
            (
                new_ulid(),
                json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "candidate_name": candidate_name,
                        "candidate_description": candidate_desc,
                        "nearest_skill_id": nearest_id,
                        "cosine": nearest_sim,
                        "reason": AuditReason.LEAKY_BUCKET_BLOCK.value,
                        "daily_new_skill_cap": self.daily_new_skill_cap,
                    }
                ),
                iso_utc(),
            ),
        )

    def decision_log_payload(self, decisions: list[ResolveDecision]) -> str:
        """Return stable JSON for debug output."""
        return json.dumps([decision.__dict__ for decision in decisions], sort_keys=True)
