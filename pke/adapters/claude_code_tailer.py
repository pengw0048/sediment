"""Claude Code transcript tailer.

The implementation can parse existing JSONL transcript files for backfill and
keeps file-offset state in adapter_state when run by the daemon.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pke.evidence.models import (
    EvidenceEvent,
    EvidenceModality,
    EvidenceRole,
    EvidenceTurn,
    parse_time,
    sha256_hex,
    utc_now,
)


def event_from_jsonl_message(
    path: Path, line_number: int, payload: dict[str, Any]
) -> EvidenceEvent | None:
    """Convert one Claude Code transcript JSON object into EvidenceEvent."""
    role_text = str(payload.get("role") or payload.get("type") or "user")
    role = {
        "assistant": EvidenceRole.ASSISTANT,
        "tool_use": EvidenceRole.TOOL_CALL,
        "tool_result": EvidenceRole.TOOL_RESULT,
        "system": EvidenceRole.SYSTEM,
    }.get(role_text, EvidenceRole.USER)
    content = payload.get("content") or payload.get("text") or payload.get("message") or ""
    if isinstance(content, list):
        content = json.dumps(content, sort_keys=True)
    if not str(content).strip():
        return None
    session_id = path.stem
    timestamp = parse_time(payload.get("timestamp") or payload.get("created_at"))
    turn = EvidenceTurn(
        role=role,
        modality=EvidenceModality.TOOL_IO
        if role.name.startswith("TOOL")
        else EvidenceModality.TEXT,
        content=str(content),
        tool_name=str(payload.get("tool_name")) if payload.get("tool_name") else None,
    )
    external_id = sha256_hex(f"{session_id}:{line_number}:{str(content)[:1024]}")
    return EvidenceEvent(
        source="claude_code_tail",
        external_id=external_id,
        conversation_id=f"cc_{session_id}",
        turn_index=line_number,
        occurred_at=timestamp,
        ingested_at=utc_now(),
        turns=[turn],
        app="claude_code",
        model=str(payload.get("model")) if payload.get("model") else None,
        workspace=str(path.parent) if path.parent else None,
        tags=["backfill"],
    )


def parse_transcript_file(
    path: Path, *, max_bytes: int | None = 2 * 1024 * 1024
) -> list[EvidenceEvent]:
    """Parse a Claude Code JSONL transcript file."""
    data = path.read_bytes()
    if max_bytes is not None and len(data) > max_bytes:
        data = data[-max_bytes:]
        first_newline = data.find(b"\n")
        if first_newline >= 0:
            data = data[first_newline + 1 :]
    events: list[EvidenceEvent] = []
    for idx, raw_line in enumerate(data.decode("utf-8", errors="ignore").splitlines()):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            event = event_from_jsonl_message(path, idx, payload)
            if event is not None:
                events.append(event)
    return events
