"""SQLite vacuum and WAL checkpoint job."""

from pke.db.sqlite import SQLiteStore


def run(sqlite: SQLiteStore) -> None:
    """Checkpoint WAL and vacuum the local database."""
    sqlite.conn.execute("PRAGMA wal_checkpoint(FULL)")
    sqlite.conn.execute("VACUUM")
