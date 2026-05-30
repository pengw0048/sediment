"""Application container for Sediment.

The App owns settings, database connections, and the layer services used by
CLI, web, adapters, review, and maintenance jobs.
"""

from __future__ import annotations

from dataclasses import dataclass

from pke.config.settings import Settings
from pke.db.sqlite import SQLiteStore
from pke.evidence.store import EvidenceStore


@dataclass(kw_only=True, slots=True)
class App:
    """Dependency container shared by every surface."""

    settings: Settings
    sqlite: SQLiteStore
    evidence: EvidenceStore

    @classmethod
    def create(cls, *, settings: Settings | None = None) -> App:
        """Create an application container and initialize SQLite schema."""
        resolved = settings or Settings.load()
        sqlite = SQLiteStore(path=resolved.evidence_db_path)
        sqlite.initialize()
        return cls(settings=resolved, sqlite=sqlite, evidence=EvidenceStore(sqlite=sqlite))

    def close(self) -> None:
        """Close owned resources."""
        self.sqlite.close()
