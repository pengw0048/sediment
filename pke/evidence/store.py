"""SQLite writer and reader for the append-only evidence log."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import EvidenceEvent, content_hash, iso_utc, new_ulid, normalize_event
from pke.evidence.redact import redact_text


class IngestStatus(str):
    """String constants returned by ingest calls."""

    NEW = "new"
    DUP_EXACT = "dup_exact"
    DUP_MERGED = "dup_merged"


@dataclass(frozen=True, kw_only=True, slots=True)
class IngestResult:
    """Result of an evidence insert attempt."""

    status: str
    evidence_id: str | None


@dataclass(kw_only=True, slots=True)
class EvidenceStore:
    """Append-only evidence persistence API."""

    sqlite: SQLiteStore

    def add(self, event: EvidenceEvent) -> IngestResult:
        """Normalize and append one evidence event with exact deduplication."""
        norm = normalize_event(event)
        redacted_content = redact_text(norm.content_text)
        digest = content_hash(norm)
        metadata = json.dumps(norm.to_metadata(), sort_keys=True, separators=(",", ":"))
        existing = self.sqlite.conn.execute(
            """
            SELECT id FROM evidence_events
            WHERE source = ? AND source_session_id = ? AND content_hash = ?
            """,
            (norm.source, norm.conversation_id, digest),
        ).fetchone()
        if existing is not None:
            return IngestResult(status=IngestStatus.DUP_EXACT, evidence_id=str(existing["id"]))
        event_id = new_ulid(norm.occurred_at)
        try:
            self.sqlite.conn.execute(
                """
                INSERT INTO evidence_events (
                  id, source, source_session_id, role, content, content_hash, tool_name,
                  metadata_json, occurred_at, ingested_at, extraction_state, extraction_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL)
                """,
                (
                    event_id,
                    norm.source,
                    norm.conversation_id,
                    norm.primary_role.value,
                    redacted_content,
                    digest,
                    norm.primary_tool_name,
                    metadata,
                    iso_utc(norm.occurred_at),
                    iso_utc(norm.ingested_at),
                ),
            )
            self.sqlite.conn.commit()
        except sqlite3.IntegrityError:
            row = self.sqlite.conn.execute(
                """
                SELECT id FROM evidence_events
                WHERE source = ? AND source_session_id = ? AND content_hash = ?
                """,
                (norm.source, norm.conversation_id, digest),
            ).fetchone()
            return IngestResult(
                status=IngestStatus.DUP_EXACT,
                evidence_id=str(row["id"]) if row is not None else None,
            )
        return IngestResult(status=IngestStatus.NEW, evidence_id=event_id)

    def add_many(self, events: list[EvidenceEvent]) -> list[IngestResult]:
        """Insert many events in sequence and return one result per event."""
        return [self.add(event) for event in events]

    def list(self, *, limit: int = 50, source: str | None = None) -> list[dict[str, Any]]:
        """List recent evidence rows."""
        if source:
            rows = self.sqlite.conn.execute(
                """
                SELECT * FROM evidence_events
                WHERE source = ?
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (source, limit),
            ).fetchall()
        else:
            rows = self.sqlite.conn.execute(
                "SELECT * FROM evidence_events ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        """Return one evidence row by id."""
        row = self.sqlite.conn.execute(
            "SELECT * FROM evidence_events WHERE id = ?",
            (evidence_id,),
        ).fetchone()
        return dict(row) if row is not None else None
