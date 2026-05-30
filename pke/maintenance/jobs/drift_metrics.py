"""Daemon jobs that populate the ARCH-2 drift metrics.

* :func:`run_centroid_count` — daily snapshot of active skill_nodes.
* :func:`run_ari_week` — weekly adjusted Rand index of identity
  clustering against last week's. Falls back gracefully when sklearn or
  prior-week data is unavailable.
* :func:`run_llm_cost_30d` — placeholder until LLM call logging lands.
  Writes a value of 0.0 and a payload note so the admin dashboard can
  render a "no data yet" band rather than a missing series.
"""

from __future__ import annotations

from typing import Any

from pke.db.sqlite import SQLiteStore
from pke.quality.metrics import (
    METRIC_ARI_WEEK,
    METRIC_CENTROID_COUNT,
    METRIC_LLM_COST_30D,
    record_metric,
)


def run_centroid_count(sqlite: SQLiteStore) -> int:
    """Snapshot the count of active skill_nodes into quality_metrics."""
    row = sqlite.conn.execute(
        "SELECT COUNT(*) AS c FROM skill_nodes WHERE user_status = 'active'"
    ).fetchone()
    count = int(row["c"] or 0)
    record_metric(sqlite, metric_name=METRIC_CENTROID_COUNT, value=float(count))
    return count


def run_ari_week(sqlite: SQLiteStore) -> float | None:
    """Compute ARI vs the prior week's identity clustering and record it.

    Reads ``micro_cluster_id`` per candidate over two consecutive
    7-day windows and asks scikit-learn for the adjusted Rand index. If
    sklearn is not installed or one of the windows has fewer than two
    candidates with matching ids, returns ``None`` without writing —
    the caller treats a missing week as "no data" rather than "red".
    """
    try:
        from sklearn.metrics import adjusted_rand_score
    except ImportError:
        return None

    rows_now = sqlite.conn.execute(
        """
        SELECT id, micro_cluster_id
        FROM skill_candidates
        WHERE created_at >= datetime('now', '-7 days')
          AND micro_cluster_id IS NOT NULL
        """
    ).fetchall()
    rows_prev = sqlite.conn.execute(
        """
        SELECT id, micro_cluster_id
        FROM skill_candidates
        WHERE created_at >= datetime('now', '-14 days')
          AND created_at <  datetime('now', '-7 days')
          AND micro_cluster_id IS NOT NULL
        """
    ).fetchall()

    now_by_id = {str(r["id"]): int(r["micro_cluster_id"]) for r in rows_now}
    prev_by_id = {str(r["id"]): int(r["micro_cluster_id"]) for r in rows_prev}
    shared = sorted(set(now_by_id) & set(prev_by_id))
    if len(shared) < 2:
        return None
    labels_prev = [prev_by_id[i] for i in shared]
    labels_now = [now_by_id[i] for i in shared]
    ari = float(adjusted_rand_score(labels_prev, labels_now))
    record_metric(
        sqlite,
        metric_name=METRIC_ARI_WEEK,
        value=ari,
        payload={"shared_candidate_count": len(shared)},
    )
    return ari


def run_llm_cost_30d(sqlite: SQLiteStore) -> float:
    """Placeholder until llm_call_log exists; writes a 0 with a note."""
    payload: dict[str, object] = {
        "note": "llm_call_log not yet implemented; cost reporting disabled"
    }
    record_metric(
        sqlite,
        metric_name=METRIC_LLM_COST_30D,
        value=0.0,
        payload=payload,
    )
    return 0.0


def run(app: Any) -> dict[str, object]:
    """Run all three samplers in one daemon tick."""
    sqlite = app.sqlite
    count = run_centroid_count(sqlite)
    ari = run_ari_week(sqlite)
    cost = run_llm_cost_30d(sqlite)
    return {"centroid_count": count, "ari_week": ari, "llm_cost_30d": cost}


__all__ = [
    "run",
    "run_ari_week",
    "run_centroid_count",
    "run_llm_cost_30d",
]
