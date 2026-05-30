"""Nightly mastery decay job."""

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc


def run(sqlite: SQLiteStore) -> None:
    """Apply a light retrievability decay tick."""
    sqlite.execute(
        """
        UPDATE skill_mastery_state
        SET unaided_retrievability = max(0, unaided_retrievability - 0.01),
            updated_at = ?
        """,
        (iso_utc(),),
    )
