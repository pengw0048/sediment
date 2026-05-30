"""Adapter protocol shared by every input source.

Adapters can observe external tools, but they only emit EvidenceEvent objects.
Redaction, deduplication, persistence, and fan-out happen centrally.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pke.evidence.models import EvidenceEvent
from pke.evidence.queue import EvidenceQueue


class AdapterState(StrEnum):
    """Lifecycle state of an adapter."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPING = "stopping"


@dataclass(kw_only=True, slots=True)
class AdapterHealth:
    """Snapshot of adapter health for admin surfaces."""

    state: AdapterState
    last_event_at: float | None
    events_emitted: int
    errors_last_hour: int
    detail: str


@dataclass(kw_only=True, slots=True)
class AdapterConfig:
    """Per-adapter configuration."""

    enabled: bool
    source_id: str
    options: dict[str, object]


@dataclass(kw_only=True, slots=True)
class AdapterContext:
    """Runtime context given to adapters."""

    queue: EvidenceQueue
    metadata: dict[str, Any]

    async def emit(self, event: EvidenceEvent) -> None:
        """Emit one event into the central queue."""
        await self.queue.put(event)


@runtime_checkable
class InputAdapter(Protocol):
    """Every input adapter implements this protocol."""

    name: str
    version: str

    async def start(self, *, config: AdapterConfig) -> None:
        """Initialize resources."""

    async def stop(self) -> None:
        """Release resources idempotently."""

    async def events(self) -> AsyncIterator[EvidenceEvent]:
        """Yield events until stopped."""

    async def health(self) -> AdapterHealth:
        """Return a cheap health snapshot."""

    async def backfill(self, *, since: float | None = None) -> AsyncIterator[EvidenceEvent]:
        """Optionally import historical events."""
        if False:
            yield
