"""Skill alias table helpers."""

from __future__ import annotations

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc, new_ulid


def add_alias(sqlite: SQLiteStore, *, skill_id: str, alias: str, source: str = "auto") -> None:
    """Add one alias if absent."""
    sqlite.execute(
        """
        INSERT OR IGNORE INTO skill_aliases(id, skill_id, alias, source, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (new_ulid(), skill_id, alias, source, iso_utc()),
    )
