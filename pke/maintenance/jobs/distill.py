"""Cross-encoder distillation placeholder.

Real distillation (training ``cross-encoder/ms-marco-MiniLM-L-6-v2`` on
accumulated review labels) is tracked as BLOCKER M9 follow-up. Until that
lands, this job logs whether the dataset is large enough so the scheduler
has a real callable to invoke and an operator can see progress toward the
5000-label threshold without diving into SQL.
"""

from pke.db.sqlite import SQLiteStore


def ready(sqlite: SQLiteStore) -> bool:
    """Return whether enough labels exist for local distillation."""
    row = sqlite.conn.execute("SELECT count(*) AS n FROM review_answers").fetchone()
    return int(row["n"] if row else 0) >= 5000


def run(sqlite: SQLiteStore) -> int:
    """Scheduler entry point: count review labels available for distillation.

    Returns the current count so admin dashboards can show progress. When
    BLOCKER M9 lands this becomes a real training run; until then it is a
    deliberately read-only check.
    """
    row = sqlite.conn.execute("SELECT count(*) AS n FROM review_answers").fetchone()
    return int(row["n"] if row else 0)
