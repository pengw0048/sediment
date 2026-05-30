"""Manual evidence adapter used by `pke evidence add`."""

from __future__ import annotations

import uuid

from pke.evidence.models import (
    EvidenceEvent,
    EvidenceModality,
    EvidenceRole,
    EvidenceTurn,
    parse_time,
    sha256_hex,
    utc_now,
)


def build_manual_event(
    *,
    user: str,
    assistant: str = "",
    app: str = "manual",
    tags: list[str] | None = None,
    conversation_id: str | None = None,
    occurred_at: str | float | int | None = None,
) -> EvidenceEvent:
    """Build a manual evidence event from CLI fields."""
    occurred = parse_time(occurred_at)
    conv_id = conversation_id or f"manual_{uuid.uuid4().hex}"
    turns = [
        EvidenceTurn(role=EvidenceRole.USER, modality=EvidenceModality.TEXT, content=user),
    ]
    if assistant:
        turns.append(
            EvidenceTurn(
                role=EvidenceRole.ASSISTANT,
                modality=EvidenceModality.TEXT,
                content=assistant,
            )
        )
    external_id = sha256_hex(f"{conv_id}:{occurred}:{user}:{assistant}")
    return EvidenceEvent(
        source="manual_cli",
        external_id=external_id,
        conversation_id=conv_id,
        turn_index=0,
        occurred_at=occurred,
        ingested_at=utc_now(),
        turns=turns,
        app=app,
        tags=tags or [],
    )
