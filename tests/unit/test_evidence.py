"""Evidence layer tests."""

import sqlite3

import pytest

from pke.adapters.manual_cli import build_manual_event
from pke.evidence.models import EvidenceModality, EvidenceRole, EvidenceTurn, normalize_event
from pke.evidence.store import IngestStatus


def test_manual_evidence_ingests_and_dedups(app):
    event = build_manual_event(
        user="How do I use async context managers?", assistant="Use __aenter__."
    )
    first = app.evidence.add(event)
    second = app.evidence.add(event)

    assert first.status == IngestStatus.NEW
    assert second.status == IngestStatus.DUP_EXACT
    rows = app.evidence.list()
    assert len(rows) == 1
    assert rows[0]["source"] == "manual_cli"


def test_append_only_protects_original_content(app):
    event = build_manual_event(user="token = sk-aaaaaaaaaaaaaaaaaaaaaaaa")
    result = app.evidence.add(event)
    assert result.evidence_id is not None
    with pytest.raises(sqlite3.IntegrityError):
        app.sqlite.conn.execute(
            "UPDATE evidence_events SET content = 'changed' WHERE id = ?",
            (result.evidence_id,),
        )


def test_normalize_truncates_long_turn():
    event = build_manual_event(user="x" * (70 * 1024))
    normalized = normalize_event(event)
    assert normalized.turns[0].truncated
    assert len(normalized.turns[0].content.encode()) <= 64 * 1024


def test_first_turn_must_be_user_or_tool_result():
    event = build_manual_event(user="hello")
    event.turns[0] = EvidenceTurn(
        role=EvidenceRole.ASSISTANT,
        modality=EvidenceModality.TEXT,
        content="bad",
    )
    with pytest.raises(ValueError):
        normalize_event(event)


def test_cross_source_dedup_merges_metadata_not_inserts(app):
    """EvidenceStore records a second observer into evidence_observers.

    The original evidence_events row stays untouched (append-only).
    """
    from dataclasses import replace

    event_hook = build_manual_event(user="How do I configure FastAPI routes?")
    # Mutate the source to simulate a Claude Code hook ingest.
    event_hook = replace(event_hook, source="claude_code_hook", tags=("hook",))
    first = app.evidence.add(event_hook)
    assert first.status == IngestStatus.NEW
    first_id = first.evidence_id

    # Same content + role, observed by a different adapter (tailer) within
    # the dedup window.
    event_tailer = replace(event_hook, source="claude_code_tail", tags=("tailer",))
    second = app.evidence.add(event_tailer)
    assert second.status == IngestStatus.DUP_MERGED
    assert second.evidence_id == first_id

    # Only one evidence row should exist; the second observer is recorded
    # in the evidence_observers side-table (append-only invariant intact).
    rows = app.evidence.list()
    assert len(rows) == 1

    observers = app.sqlite.conn.execute(
        "SELECT source, tags_json FROM evidence_observers WHERE evidence_id = ?",
        (first_id,),
    ).fetchall()
    observer_sources = {row["source"] for row in observers}
    assert observer_sources == {"claude_code_tail"}
    # The original row's source ("claude_code_hook") is canonical and not
    # re-listed in evidence_observers, which only records *additional* observers.


def test_cross_source_dedup_respects_window(app):
    """EvidenceStore merges across sources only inside the dedup window."""
    from dataclasses import replace

    from pke.evidence.store import _CROSS_SOURCE_WINDOW_SECONDS

    event = build_manual_event(user="How do I configure FastAPI routes?")
    event_a = replace(event, source="claude_code_hook")
    first = app.evidence.add(event_a)
    assert first.status == IngestStatus.NEW

    # Same content from a different source but far outside the window: should
    # insert as a NEW row, not DUP_MERGED.
    event_b = replace(
        event_a,
        source="chatgpt_history",
        occurred_at=event_a.occurred_at + _CROSS_SOURCE_WINDOW_SECONDS + 60.0,
    )
    second = app.evidence.add(event_b)
    assert second.status == IngestStatus.NEW
    assert second.evidence_id != first.evidence_id


def test_schema_version_table_records_applied_migrations(app):
    """apply_pending writes one row per applied migration into schema_version."""
    rows = app.sqlite.conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    versions = [row["version"] for row in rows]
    assert 1 in versions
    assert 2 in versions  # 0002_cross_source_dedup
