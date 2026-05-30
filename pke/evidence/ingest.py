"""Central evidence ingest function used by adapters and web endpoints."""

from __future__ import annotations

from pke.evidence.models import EvidenceEvent
from pke.evidence.store import EvidenceStore, IngestResult


async def ingest(store: EvidenceStore, event: EvidenceEvent) -> IngestResult:
    """Async wrapper around the synchronous SQLite evidence writer."""
    return store.add(event)
