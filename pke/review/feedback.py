"""User feedback actions on skills and items."""

from __future__ import annotations

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc


def drop_skill(sqlite: SQLiteStore, skill_id: str) -> None:
    """Permanently hide a skill from selection and intervention."""
    sqlite.execute(
        "UPDATE skill_nodes SET user_status = 'dropped', updated_at = ? WHERE id = ?",
        (iso_utc(), skill_id),
    )


def already_known(sqlite: SQLiteStore, skill_id: str) -> None:
    """Mark a skill as already known."""
    sqlite.execute(
        "UPDATE skill_nodes SET user_status = 'already_known', updated_at = ? WHERE id = ?",
        (iso_utc(), skill_id),
    )
