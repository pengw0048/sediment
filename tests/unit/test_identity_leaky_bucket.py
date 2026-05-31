"""Leaky-bucket cap on IdentityResolver new-skill creation.

When the daily cap is reached, candidates that would otherwise become
brand-new skills are queued in ``pending_audits`` with a
``candidate_review`` row carrying ``reason='leaky_bucket_block'`` in
its payload, and the corresponding ``skill_candidates`` row flips to
``resolution_state='pending_audit'`` rather than ``auto``. A noisy
extraction run can no longer flood the graph with thousands of nodes
in a single day.
"""

from __future__ import annotations

import json

from pke.identity.ann_index import AnnIndex
from pke.identity.embedder import Embedder
from pke.identity.resolver import IdentityResolver


def test_create_skill_blocks_when_daily_cap_reached(app):
    """``_create_skill`` returns None once the day's quota is spent.

    Cap is set to 1: the first call succeeds and inserts a row; the
    second call must return None and leave ``skill_nodes`` unchanged.
    """
    embedder = Embedder()
    resolver = IdentityResolver(
        sqlite=app.sqlite,
        embedder=embedder,
        ann=AnnIndex(),
        daily_new_skill_cap=1,
    )

    vector = embedder.embed("fastapi routes")
    first = resolver._create_skill("fastapi routes", "route declaration", vector)
    assert first is not None
    assert (
        app.sqlite.conn.execute("SELECT count(*) AS n FROM skill_nodes").fetchone()["n"] == 1
    )

    second = resolver._create_skill(
        "postgres replication lag", "bytes behind primary", embedder.embed("postgres lag")
    )
    assert second is None, "second create must be blocked by the daily cap"
    assert (
        app.sqlite.conn.execute("SELECT count(*) AS n FROM skill_nodes").fetchone()["n"] == 1
    ), "skill_nodes must not grow after the cap is hit"


def test_new_or_blocked_writes_leaky_bucket_audit(app):
    """``_new_or_blocked`` queues a candidate_review row with the right reason.

    Pre-fill ``skill_nodes`` so today's count is at the cap, then call
    ``_new_or_blocked`` directly. The candidate must be reported as
    ``pending`` and a row must land in ``pending_audits`` carrying
    ``reason='leaky_bucket_block'`` in its payload JSON.
    """
    from pke.evidence.models import iso_utc, new_ulid

    # Pre-seed one skill_node so the cap=1 bucket is already full.
    embedder = Embedder()
    vector = embedder.embed("dummy seed")
    now = iso_utc()
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding, cluster_size,
          first_seen_at, last_seen_at, created_at, updated_at
        ) VALUES (?, 'seed', '', X'', 1, ?, ?, ?, ?)
        """,
        (new_ulid(), now, now, now, now),
    )
    app.sqlite.conn.commit()

    resolver = IdentityResolver(
        sqlite=app.sqlite,
        embedder=embedder,
        ann=AnnIndex(),
        daily_new_skill_cap=1,
    )
    action, skill_id, judge = resolver._new_or_blocked(
        candidate_id="cand-1",
        candidate_name="postgres replication lag",
        candidate_desc="bytes behind primary",
        vector=vector,
        nearest_id=None,
        nearest_sim=0.0,
        judge_triggered=False,
    )
    assert action == "pending", "blocked candidate must report pending"
    assert skill_id is None
    assert judge is False

    rows = app.sqlite.conn.execute(
        "SELECT payload_json FROM pending_audits WHERE audit_type = 'candidate_review'"
    ).fetchall()
    reasons = [json.loads(r["payload_json"]).get("reason") for r in rows]
    assert "leaky_bucket_block" in reasons


def test_create_skill_cap_zero_disables_the_bucket(app):
    """``daily_new_skill_cap=0`` lets every create through."""
    embedder = Embedder()
    resolver = IdentityResolver(
        sqlite=app.sqlite,
        embedder=embedder,
        ann=AnnIndex(),
        daily_new_skill_cap=0,
    )
    for i in range(5):
        skill_id = resolver._create_skill(
            f"skill-{i}", f"desc {i}", embedder.embed(f"skill {i}")
        )
        assert skill_id is not None, f"create #{i} must succeed with cap=0"
    assert (
        app.sqlite.conn.execute("SELECT count(*) AS n FROM skill_nodes").fetchone()["n"] == 5
    )
