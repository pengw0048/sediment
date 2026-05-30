"""SQLite writer and reader for the append-only evidence log."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import (
    EvidenceEvent,
    content_hash,
    iso_utc,
    new_ulid,
    normalize_event,
)
from pke.evidence.redact import redact_text

# Lower number = higher priority. When two adapters observe the same logical
# turn, the row's canonical ``source`` is the first writer's; later writers
# contribute to ``metadata.sources_seen`` so downstream code knows the turn
# was double-observed. Source names match ``pke.evidence.models.VALID_SOURCES``.
_SOURCE_PRIORITY = {
    "claude_code_hook": 0,
    "claude_code_tail": 1,
    "cursor_tail": 1,
    "browser_ext": 2,
    "openai_proxy": 3,
    "anthropic_proxy": 3,
    "chatgpt_history": 4,
    "claude_ai_history": 4,
    "file_watcher": 5,
    "manual_cli": 6,
}

# Cross-source dedup window: two adapters reporting the same content_hash +
# role within this many seconds are treated as the same turn.
_CROSS_SOURCE_WINDOW_SECONDS = 60.0


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
        """Normalize and append one evidence event.

        Two-stage dedup:

        1. ``DUP_EXACT`` — same ``(source, source_session_id, content_hash)``
           already present: the same adapter re-emitting the same row.
        2. ``DUP_MERGED`` — same ``(content_hash, role)`` observed within
           :data:`_CROSS_SOURCE_WINDOW_SECONDS` by a different adapter:
           the same logical turn seen via a second adapter. The existing
           evidence_events row stays untouched; the second observer is
           recorded in ``evidence_observers`` keyed by
           ``(evidence_id, source)``.
        """
        norm = normalize_event(event)
        redacted_content = redact_text(norm.content_text)
        digest = content_hash(norm)
        metadata = json.dumps(norm.to_metadata(), sort_keys=True, separators=(",", ":"))

        # Stage 1: exact same-source repeat.
        existing = self.sqlite.conn.execute(
            """
            SELECT id FROM evidence_events
            WHERE source = ? AND source_session_id = ? AND content_hash = ?
            """,
            (norm.source, norm.conversation_id, digest),
        ).fetchone()
        if existing is not None:
            return IngestResult(status=IngestStatus.DUP_EXACT, evidence_id=str(existing["id"]))

        # Stage 2: cross-source dedup. Look for a recent row with the same
        # content + role from any *other* source.
        merged_id = self._try_cross_source_merge(norm=norm, digest=digest)
        if merged_id is not None:
            return IngestResult(status=IngestStatus.DUP_MERGED, evidence_id=merged_id)

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

    def _try_cross_source_merge(self, *, norm: EvidenceEvent, digest: str) -> str | None:
        """Stage 2 dedup: record the second observer; never modify the original row.

        Returns the existing row's id if a merge happened, else ``None``. The
        original evidence_events row stays untouched (append-only invariant).
        Each cross-source observation is recorded in ``evidence_observers``.
        """
        candidates = self.sqlite.conn.execute(
            """
            SELECT id, source, occurred_at
            FROM evidence_events
            WHERE content_hash = ? AND role = ? AND source != ?
            ORDER BY occurred_at DESC
            LIMIT 5
            """,
            (digest, norm.primary_role.value, norm.source),
        ).fetchall()
        if not candidates:
            return None
        target_ts = norm.occurred_at  # unix epoch seconds (float)
        for row in candidates:
            existing_dt = _parse_iso(str(row["occurred_at"]))
            if existing_dt is None:
                continue
            existing_ts = existing_dt.timestamp()
            if abs(existing_ts - target_ts) <= _CROSS_SOURCE_WINDOW_SECONDS:
                self._record_observer(
                    evidence_id=str(row["id"]),
                    source=norm.source,
                    source_session_id=norm.conversation_id,
                    observed_at=iso_utc(norm.ingested_at),
                    tags=list(norm.tags or ()),
                )
                return str(row["id"])
        return None

    def _record_observer(
        self,
        *,
        evidence_id: str,
        source: str,
        source_session_id: str,
        observed_at: str,
        tags: list[str],
    ) -> None:
        """Record that a second adapter observed the same logical turn.

        Writes one row per (evidence_id, source) pair. Idempotent.
        """
        self.sqlite.conn.execute(
            """
            INSERT OR IGNORE INTO evidence_observers(
              evidence_id, source, source_session_id, observed_at, tags_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                source,
                source_session_id,
                observed_at,
                json.dumps(sorted(tags)),
            ),
        )
        self.sqlite.conn.commit()

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


def _parse_iso(value: str) -> datetime | None:
    """Best-effort ISO-8601 parse used only for cross-source dedup windowing."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None
