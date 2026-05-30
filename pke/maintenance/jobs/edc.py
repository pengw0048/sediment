"""Extract-Define-Canonicalize merge sweep.

Runs nightly. For every pair of active skill nodes whose canonical-name
cosine exceeds :data:`NAME_COSINE_THRESHOLD`, writes an LLM-authored
definition for each phrase, embeds the definitions, and (when the
definition cosine is also high) asks a second LLM call to adjudicate the
merge. Confirmed merges land in ``pending_audits`` as
``audit_type='merge'`` so a human reviews them before any mastery state
is actually combined.

The sweep is deliberately read-only with respect to the skill graph;
applying the merge belongs to a later mastery-transfer step.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
from dataclasses import dataclass

from pke.app import App
from pke.evidence.models import iso_utc, new_ulid
from pke.extraction.llm_client import LLMClient
from pke.extraction.prompts import render as render_prompt
from pke.extraction.schema import clamp01
from pke.identity.embedder import Embedder

NAME_COSINE_THRESHOLD = 0.85
DEFINITION_COSINE_THRESHOLD = 0.85
# Hard cap on how many pairs to process per run so a sudden flood of new
# skills cannot blow up the nightly LLM budget.
MAX_PAIRS_PER_RUN = 50


@dataclass(frozen=True, kw_only=True, slots=True)
class CandidatePair:
    """One pair of skills considered for canonicalization."""

    a_id: str
    a_name: str
    b_id: str
    b_name: str
    name_cosine: float


@dataclass(frozen=True, kw_only=True, slots=True)
class EdcVerdict:
    """The LLM judge's decision on a candidate pair."""

    pair: CandidatePair
    a_definition: str
    b_definition: str
    definition_cosine: float
    verdict: str  # merge | no_merge | abstain
    confidence: float
    rationale: str


def run(app: App) -> int:
    """Scheduler entry point. Returns the number of pairs adjudicated."""
    client = app.llm_client
    if client is None:
        return 0
    embedder = Embedder()
    pairs = _find_candidate_pairs(app)
    if not pairs:
        return 0
    verdicts = asyncio.run(_adjudicate_all(client, embedder, pairs))
    _persist_verdicts(app, verdicts)
    return len(verdicts)


def _find_candidate_pairs(app: App) -> list[CandidatePair]:
    """Return pairs of active skills whose name-embedding cosine is high."""
    rows = app.sqlite.conn.execute(
        """
        SELECT id, canonical_name, embedding
        FROM skill_nodes
        WHERE user_status = 'active' AND embedding IS NOT NULL
        ORDER BY last_seen_at DESC
        LIMIT 500
        """
    ).fetchall()
    vectors: list[tuple[str, str, list[float]]] = []
    for row in rows:
        vec = _blob_to_vector(row["embedding"])
        if vec:
            vectors.append((str(row["id"]), str(row["canonical_name"]), vec))

    pairs: list[CandidatePair] = []
    for i, (a_id, a_name, a_vec) in enumerate(vectors):
        for b_id, b_name, b_vec in vectors[i + 1 :]:
            cos = _cosine(a_vec, b_vec)
            if cos >= NAME_COSINE_THRESHOLD:
                pairs.append(
                    CandidatePair(
                        a_id=a_id,
                        a_name=a_name,
                        b_id=b_id,
                        b_name=b_name,
                        name_cosine=cos,
                    )
                )
    pairs.sort(key=lambda p: p.name_cosine, reverse=True)
    return pairs[:MAX_PAIRS_PER_RUN]


async def _adjudicate_all(
    client: LLMClient, embedder: Embedder, pairs: list[CandidatePair]
) -> list[EdcVerdict]:
    """Define both phrases of every pair, then verify-merge."""
    out: list[EdcVerdict] = []
    for pair in pairs:
        a_def = await _write_definition(client, pair.a_name)
        b_def = await _write_definition(client, pair.b_name)
        def_cosine = _cosine(embedder.embed(a_def), embedder.embed(b_def))
        if def_cosine < DEFINITION_COSINE_THRESHOLD:
            # The definitions disagree, so the surface-name match was a
            # false positive. Record an abstain so the admin queue knows
            # the pipeline looked but moved on.
            out.append(
                EdcVerdict(
                    pair=pair,
                    a_definition=a_def,
                    b_definition=b_def,
                    definition_cosine=def_cosine,
                    verdict="no_merge",
                    confidence=def_cosine,
                    rationale="Definition cosine fell below threshold; surface names misleading.",
                )
            )
            continue
        payload = await client.complete_json(
            system=render_prompt("edc_verify_merge.system.j2"),
            user=render_prompt(
                "edc_verify_merge.user.j2",
                a_name=pair.a_name,
                a_definition=a_def,
                b_name=pair.b_name,
                b_definition=b_def,
                name_cosine=pair.name_cosine,
                def_cosine=def_cosine,
            ),
        )
        verdict = str(payload.get("verdict", "abstain")).lower()
        if verdict not in {"merge", "no_merge", "abstain"}:
            verdict = "abstain"
        try:
            confidence = clamp01(float(payload.get("confidence", 0.0)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            confidence = 0.0
        out.append(
            EdcVerdict(
                pair=pair,
                a_definition=a_def,
                b_definition=b_def,
                definition_cosine=def_cosine,
                verdict=verdict,
                confidence=confidence,
                rationale=str(payload.get("rationale", "")).strip(),
            )
        )
    return out


async def _write_definition(client: LLMClient, phrase: str) -> str:
    payload = await client.complete_json(
        system=render_prompt("edc_write_definition.system.j2"),
        user=render_prompt("edc_write_definition.user.j2", phrase=phrase),
    )
    return str(payload.get("definition", "")).strip()


def _persist_verdicts(app: App, verdicts: list[EdcVerdict]) -> None:
    """Write each verdict to ``pending_audits`` for human review."""
    now = iso_utc()
    for v in verdicts:
        if v.verdict == "no_merge":
            continue
        app.sqlite.conn.execute(
            """
            INSERT INTO pending_audits(id, audit_type, payload_json, created_at)
            VALUES (?, 'merge', ?, ?)
            """,
            (
                new_ulid(),
                json.dumps(
                    {
                        "kind": "edc_sweep",
                        "a_id": v.pair.a_id,
                        "a_name": v.pair.a_name,
                        "a_definition": v.a_definition,
                        "b_id": v.pair.b_id,
                        "b_name": v.pair.b_name,
                        "b_definition": v.b_definition,
                        "name_cosine": v.pair.name_cosine,
                        "definition_cosine": v.definition_cosine,
                        "verdict": v.verdict,
                        "confidence": v.confidence,
                        "rationale": v.rationale,
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
    app.sqlite.conn.commit()


def _blob_to_vector(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
