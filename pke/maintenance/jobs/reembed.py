"""Re-embed changed skill nodes."""

from pke.db.sqlite import SQLiteStore


def run(sqlite: SQLiteStore) -> int:
    """Return count of skills that would be re-embedded."""
    row = sqlite.conn.execute("SELECT count(*) AS n FROM skill_nodes").fetchone()
    return int(row["n"] if row else 0)
