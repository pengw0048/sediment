"""Claude.ai export importer."""

from __future__ import annotations

import json
import tempfile
import zipfile
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


def import_conversations(path: Path) -> list[EvidenceEvent]:
    """Import Claude.ai conversations JSON into EvidenceEvent objects."""
    conversations = json.loads(path.read_text(encoding="utf-8"))
    events: list[EvidenceEvent] = []
    for conversation in conversations:
        if not isinstance(conversation, dict):
            continue
        conv_id = str(conversation.get("uuid") or conversation.get("id") or "unknown")
        messages = conversation.get("chat_messages", [])
        pending_human: dict[str, Any] | None = None
        turn_index = 0
        for message in messages:
            if not isinstance(message, dict):
                continue
            sender = message.get("sender")
            if sender == "human":
                pending_human = message
                continue
            if sender != "assistant" or pending_human is None:
                continue
            user_text = str(pending_human.get("text") or "")
            assistant_text = str(message.get("text") or "")
            events.append(
                EvidenceEvent(
                    source="claude_ai_history",
                    external_id=sha256_hex(
                        str(pending_human.get("uuid") or f"{conv_id}:{turn_index}")
                    ),
                    conversation_id=f"claude_web_{conv_id}",
                    turn_index=turn_index,
                    occurred_at=parse_time(pending_human.get("created_at")),
                    ingested_at=utc_now(),
                    turns=[
                        EvidenceTurn(
                            role=EvidenceRole.USER,
                            modality=EvidenceModality.TEXT,
                            content=user_text,
                        ),
                        EvidenceTurn(
                            role=EvidenceRole.ASSISTANT,
                            modality=EvidenceModality.TEXT,
                            content=assistant_text,
                        ),
                    ],
                    app="claude_web",
                    tags=["backfill"],
                )
            )
            pending_human = None
            turn_index += 1
    return events


def import_claude_archive(path: Path) -> list[EvidenceEvent]:
    """Import a Claude.ai export zip or conversations.json file."""
    if path.suffix.lower() != ".zip":
        return import_conversations(path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(tmp_path)
        target = next(tmp_path.rglob("conversations.json"))
        return import_conversations(target)
