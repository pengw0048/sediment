"""Evidence event schema and normalization helpers.

Evidence is append-only input from adapters. It is the immutable source used to
rebuild every derived view.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

MAX_TURN_BYTES = 64 * 1024
MAX_EVENT_BYTES = 256 * 1024

VALID_SOURCES = {
    "claude_code_hook",
    "claude_code_tail",
    "cursor_tail",
    "browser_ext",
    "browser_ext_chatgpt",
    "browser_ext_claude_ai",
    "browser_ext_gemini",
    "chatgpt_history",
    "claude_ai_history",
    "openai_proxy",
    "manual_cli",
    "file_watcher",
    "anthropic_proxy",
}


class EvidenceRole(StrEnum):
    """Role of one turn in an evidence event."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"


class EvidenceModality(StrEnum):
    """Modality carried by one turn."""

    TEXT = "text"
    CODE = "code"
    IMAGE_REF = "image_ref"
    TOOL_IO = "tool_io"


@dataclass(kw_only=True, slots=True)
class EvidenceTurn:
    """One message inside a conversation turn."""

    role: EvidenceRole
    modality: EvidenceModality
    content: str
    tool_name: str | None = None
    tool_args_json: str | None = None
    truncated: bool = False

    def normalized(self) -> EvidenceTurn:
        """Return a clipped turn that respects byte limits."""
        raw = self.content.encode("utf-8")
        if len(raw) <= MAX_TURN_BYTES:
            return self
        clipped = raw[:MAX_TURN_BYTES].decode("utf-8", errors="ignore")
        return replace(self, content=clipped, truncated=True)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "role": self.role.value,
            "modality": self.modality.value,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_args_json": self.tool_args_json,
            "truncated": self.truncated,
        }


@dataclass(kw_only=True, slots=True)
class EvidenceEvent:
    """The unit of ingestion: one user/AI interaction turn."""

    source: str
    external_id: str
    conversation_id: str
    turn_index: int
    occurred_at: float
    ingested_at: float
    turns: list[EvidenceTurn]
    app: str
    model: str | None = None
    workspace: str | None = None
    user_agent: str | None = None
    locale: str | None = None
    tags: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def content_text(self) -> str:
        """Return canonical user-visible text for flattening into SQLite."""
        return "\n\n".join(f"{turn.role.value}: {turn.content}" for turn in self.turns)

    @property
    def primary_role(self) -> EvidenceRole:
        """Return the role stored in the flattened evidence_events row."""
        return self.turns[0].role

    @property
    def primary_tool_name(self) -> str | None:
        """Return the first tool name if present."""
        for turn in self.turns:
            if turn.tool_name:
                return turn.tool_name
        return None

    def to_metadata(self) -> dict[str, Any]:
        """Return adapter and original schema fields for metadata_json."""
        return {
            "external_id": self.external_id,
            "turn_index": self.turn_index,
            "app": self.app,
            "model": self.model,
            "workspace": self.workspace,
            "user_agent": self.user_agent,
            "locale": self.locale,
            "tags": self.tags,
            "extra": self.extra,
            "turns": [turn.to_dict() for turn in self.turns],
        }


def utc_now() -> float:
    """Return current Unix timestamp in UTC seconds."""
    return time.time()


def iso_utc(ts: float | None = None) -> str:
    """Return ISO-8601 UTC text with millisecond precision and Z suffix."""
    value = datetime.fromtimestamp(ts if ts is not None else utc_now(), tz=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str | float | int | None) -> float:
    """Parse an ISO timestamp or Unix timestamp into seconds."""
    if value is None:
        return utc_now()
    if isinstance(value, int | float):
        return float(value)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).timestamp()


def sha256_hex(text: str) -> str:
    """Return a SHA-256 lowercase hex digest for text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_ulid(ts: float | None = None) -> str:
    """Generate a sortable 26-character ULID-like identifier."""
    timestamp_ms = int((ts if ts is not None else utc_now()) * 1000)
    random_bytes = hashlib.sha256(f"{timestamp_ms}:{time.time_ns()}".encode()).digest()[:10]
    raw = timestamp_ms.to_bytes(6, "big") + random_bytes
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()[:26]


def content_hash(event: EvidenceEvent) -> str:
    """Return the canonical content hash used for deduplication."""
    parts: list[str] = []
    for turn in event.turns:
        if turn.role in {EvidenceRole.USER, EvidenceRole.ASSISTANT, EvidenceRole.TOOL_RESULT}:
            parts.append(" ".join(turn.content.lower().split()))
    return sha256_hex("\n".join(parts))


def normalize_event(event: EvidenceEvent) -> EvidenceEvent:
    """Validate, clip, and clamp an event before persistence."""
    if event.source not in VALID_SOURCES:
        raise ValueError(f"unknown evidence source: {event.source}")
    if len(event.external_id) != 64 or event.external_id.lower() != event.external_id:
        raise ValueError("external_id must be 64-char lowercase SHA-256 hex")
    int(event.external_id, 16)
    if event.turn_index < 0:
        raise ValueError("turn_index must be non-negative")
    if not event.turns:
        raise ValueError("event must contain at least one turn")
    if event.turns[0].role not in {EvidenceRole.USER, EvidenceRole.TOOL_RESULT}:
        raise ValueError("first turn must be user or tool_result")

    now = utc_now()
    occurred = min(event.occurred_at, now + 60)
    ingested = min(max(event.ingested_at, occurred), now + 60)
    turns = [turn.normalized() for turn in event.turns]
    total = sum(len(turn.content.encode("utf-8")) for turn in turns)
    if total > MAX_EVENT_BYTES:
        shrink = total - MAX_EVENT_BYTES
        ordered = sorted(range(len(turns)), key=lambda idx: len(turns[idx].content), reverse=True)
        for idx in ordered:
            turn = turns[idx]
            raw = turn.content.encode("utf-8")
            if not raw:
                continue
            take = min(shrink, max(0, len(raw) - 1024))
            if take <= 0:
                continue
            clipped = raw[: len(raw) - take].decode("utf-8", errors="ignore")
            turns[idx] = replace(turn, content=clipped, truncated=True)
            shrink -= take
            if shrink <= 0:
                break
    return replace(event, occurred_at=occurred, ingested_at=ingested, turns=turns)
