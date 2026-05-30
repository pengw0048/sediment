"""Cursor transcript reader.

Cursor files are opened read-only. The parsers are best-effort because Cursor's
internal schema is not officially stable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pke.evidence.models import (
    EvidenceEvent,
    EvidenceModality,
    EvidenceRole,
    EvidenceTurn,
    parse_time,
    sha256_hex,
    utc_now,
)


def parse_agent_transcript(path: Path) -> list[EvidenceEvent]:
    """Parse Cursor agent-transcripts JSONL files."""
    events: list[EvidenceEvent] = []
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        kind = str(payload.get("type") or "")
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        role = {
            "assistant_message": EvidenceRole.ASSISTANT,
            "tool_invocation": EvidenceRole.TOOL_CALL,
            "tool_response": EvidenceRole.TOOL_RESULT,
        }.get(kind, EvidenceRole.USER)
        text = body.get("text") or body.get("content") or body.get("message") or ""
        if not str(text).strip():
            continue
        session_id = path.stem
        events.append(
            EvidenceEvent(
                source="cursor_tail",
                external_id=sha256_hex(f"{path}:{idx}:{text}"),
                conversation_id=f"cursor_jsonl_{session_id}",
                turn_index=idx,
                occurred_at=parse_time(body.get("timestamp") or body.get("createdAt")),
                ingested_at=utc_now(),
                turns=[
                    EvidenceTurn(
                        role=role,
                        modality=EvidenceModality.TOOL_IO
                        if role in {EvidenceRole.TOOL_CALL, EvidenceRole.TOOL_RESULT}
                        else EvidenceModality.TEXT,
                        content=str(text),
                    )
                ],
                app="cursor",
                model=str(body.get("model")) if body.get("model") else None,
                workspace=str(path.parent),
            )
        )
    return events


def parse_state_vscdb(path: Path) -> list[EvidenceEvent]:
    """Read Cursor Chat state.vscdb in read-only mode."""
    uri = f"file:{path}?mode=ro&immutable=0"
    events: list[EvidenceEvent] = []
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT value FROM ItemTable WHERE key='workbench.panel.aichat.view.aichat.chatdata'"
        ).fetchall()
    for row in rows:
        try:
            data = json.loads(str(row["value"]))
        except json.JSONDecodeError:
            continue
        tabs = data.get("tabs", []) if isinstance(data, dict) else []
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            tab_id = str(tab.get("tabId") or tab.get("id") or "unknown")
            bubbles = tab.get("bubbles", [])
            for idx, bubble in enumerate(bubbles):
                if not isinstance(bubble, dict):
                    continue
                text = bubble.get("text") or bubble.get("content") or ""
                if not str(text).strip():
                    continue
                role = (
                    EvidenceRole.ASSISTANT
                    if bubble.get("type") == "assistant"
                    else EvidenceRole.USER
                )
                events.append(
                    EvidenceEvent(
                        source="cursor_tail",
                        external_id=sha256_hex(f"{tab_id}:{idx}:{text}"),
                        conversation_id=f"cursor_chat_{tab_id}",
                        turn_index=idx,
                        occurred_at=parse_time(bubble.get("createdAt")),
                        ingested_at=utc_now(),
                        turns=[
                            EvidenceTurn(
                                role=role,
                                modality=EvidenceModality.TEXT,
                                content=str(text),
                            )
                        ],
                        app="cursor",
                        model=str(bubble.get("model")) if bubble.get("model") else None,
                    )
                )
    return events
