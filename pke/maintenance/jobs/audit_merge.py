"""Detect over-merged clusters."""

from pke.db.sqlite import SQLiteStore


def run(sqlite: SQLiteStore) -> int:
    """Return open merge audit count."""
    row = sqlite.conn.execute(
        "SELECT count(*) AS n FROM pending_audits WHERE audit_type = 'merge' AND resolved_at IS NULL"
    ).fetchone()
    return int(row["n"] if row else 0)
