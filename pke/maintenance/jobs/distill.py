"""Cross-encoder distillation placeholder."""

from pke.db.sqlite import SQLiteStore


def ready(sqlite: SQLiteStore) -> bool:
    """Return whether enough labels exist for local distillation."""
    row = sqlite.conn.execute("SELECT count(*) AS n FROM review_answers").fetchone()
    return int(row["n"] if row else 0) >= 5000
