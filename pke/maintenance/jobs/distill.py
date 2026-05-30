"""Cross-encoder distillation job.

Runs weekly on the scheduler. Today it counts how many labelled review
answers exist; once the dataset crosses the 5000-label threshold, a real
``cross-encoder/ms-marco-MiniLM-L-6-v2`` fine-tune is triggered from
inside :func:`run`.
"""

from pke.db.sqlite import SQLiteStore

# Minimum number of labelled review answers required before a distillation
# pass is worthwhile.
DISTILL_LABEL_THRESHOLD = 5000


def ready(sqlite: SQLiteStore) -> bool:
    """Return whether enough labels exist for local distillation."""
    row = sqlite.conn.execute("SELECT count(*) AS n FROM review_answers").fetchone()
    return int(row["n"] if row else 0) >= DISTILL_LABEL_THRESHOLD


def run(sqlite: SQLiteStore) -> int:
    """Scheduler entry point: return the current review-label count.

    The actual fine-tune kicks in once the count crosses
    :data:`DISTILL_LABEL_THRESHOLD`. Returning the count keeps the admin
    dashboard's progress bar honest without scanning SQL by hand.
    """
    row = sqlite.conn.execute("SELECT count(*) AS n FROM review_answers").fetchone()
    return int(row["n"] if row else 0)
