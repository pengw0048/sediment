"""SQLite connection manager.

The store applies WAL, foreign keys, NORMAL sync, and a busy timeout on every
connection. SQLite remains the source of truth for evidence, skill nodes, review
state, settings, and audit metadata.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pke.db.migrate import apply_pending


@dataclass(kw_only=True, slots=True)
class SQLiteStore:
    """Thin SQLite wrapper with schema migration support."""

    path: Path
    _conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Return an open connection.

        ``check_same_thread=False`` is required because some adapters
        (the OpenAI / Anthropic HTTP proxies, the browser-extension
        endpoint) ingest evidence from FastAPI request handlers running
        on a uvicorn worker thread, not the thread that constructed the
        :class:`App`. SQLite still serializes writes via WAL plus
        Python's GIL; we don't run concurrent writers.
        """
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._apply_pragmas(self._conn)
        return self._conn

    def initialize(self) -> None:
        """Apply all pending migrations."""
        apply_pending(self.conn)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute one SQL statement and commit it."""
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> sqlite3.Cursor:
        """Execute one SQL statement for many parameter tuples."""
        cur = self.conn.executemany(sql, seq)
        self.conn.commit()
        return cur

    def close(self) -> None:
        """Close the connection if it is open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _apply_pragmas(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
