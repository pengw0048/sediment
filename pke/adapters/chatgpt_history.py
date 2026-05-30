"""ChatGPT history JSON importer."""

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
    sha256_hex,
    utc_now,
)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", {})
    if isinstance(content, dict):
        parts = content.get("parts", [])
        if isinstance(parts, list):
            return "\n".join(str(part) for part in parts)
        return str(content.get("text") or "")
    return str(content)


def _linear_messages(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    mapping = conversation.get("mapping", {})
    node_id = conversation.get("current_node")
    chain: list[dict[str, Any]] = []
    while node_id and isinstance(mapping, dict):
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            break
        message = node.get("message")
        if isinstance(message, dict):
            chain.append(message)
        node_id = node.get("parent")
    return list(reversed(chain))


def import_conversations_json(path: Path) -> list[EvidenceEvent]:
    """Import ChatGPT conversations.json into EvidenceEvent objects."""
    conversations = json.loads(path.read_text(encoding="utf-8"))
    events: list[EvidenceEvent] = []
    for conversation in conversations:
        if not isinstance(conversation, dict):
            continue
        conv_id = str(
            conversation.get("id") or sha256_hex(json.dumps(conversation, sort_keys=True))[:16]
        )
        messages = _linear_messages(conversation)
        turn_index = 0
        pending_user: dict[str, Any] | None = None
        for message in messages:
            author = message.get("author", {})
            role = author.get("role") if isinstance(author, dict) else None
            if role == "user":
                pending_user = message
                continue
            if role != "assistant" or pending_user is None:
                continue
            user_text = _message_text(pending_user)
            assistant_text = _message_text(message)
            upstream_id = str(pending_user.get("id") or f"{conv_id}:{turn_index}")
            metadata = (
                message.get("metadata", {}) if isinstance(message.get("metadata"), dict) else {}
            )
            events.append(
                EvidenceEvent(
                    source="chatgpt_history",
                    external_id=sha256_hex(upstream_id),
                    conversation_id=f"chatgpt_{conv_id}",
                    turn_index=turn_index,
                    occurred_at=float(
                        pending_user.get("create_time")
                        or conversation.get("create_time")
                        or utc_now()
                    ),
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
                    app="chatgpt_web",
                    model=str(metadata.get("model_slug")) if metadata.get("model_slug") else None,
                    tags=["backfill"],
                )
            )
            pending_user = None
            turn_index += 1
    return events


def import_chatgpt_archive(path: Path) -> list[EvidenceEvent]:
    """Import a ChatGPT export zip or conversations.json file."""
    if path.suffix.lower() != ".zip":
        return import_conversations_json(path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(tmp_path)
        target = next(tmp_path.rglob("conversations.json"))
        return import_conversations_json(target)
