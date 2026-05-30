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
