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


@dataclass(kw_only=True, slots=True)
class _AdapterBase:
    """Shared bookkeeping for the concrete input adapters.

    Tracks lifecycle state and event counts so every adapter can satisfy
    :meth:`health` with the same shape. Subclasses only need to override
    :meth:`events` and (optionally) :meth:`backfill`; ``start`` /
    ``stop`` / ``health`` come for free.
    """

    name: str = "unnamed"
    version: str = "0.1.0"
    _state: AdapterState = AdapterState.STOPPED
    _events_emitted: int = 0
    _errors_last_hour: int = 0
    _last_event_at: float | None = None
    _detail: str = "idle"

    async def start(self, *, config: AdapterConfig) -> None:
        """Mark the adapter running. Subclasses override to acquire resources."""
        del config
        self._state = AdapterState.RUNNING

    async def stop(self) -> None:
        """Mark the adapter stopped. Subclasses override to release resources."""
        self._state = AdapterState.STOPPED

    async def events(self) -> AsyncIterator[EvidenceEvent]:
        """Default ``events`` is empty so passive adapters do not block startup."""
        if False:
            yield

    async def health(self) -> AdapterHealth:
        """Cheap health snapshot derived from the bookkeeping fields."""
        return AdapterHealth(
            state=self._state,
            last_event_at=self._last_event_at,
            events_emitted=self._events_emitted,
            errors_last_hour=self._errors_last_hour,
            detail=self._detail,
        )

    async def backfill(self, *, since: float | None = None) -> AsyncIterator[EvidenceEvent]:
        """Default ``backfill`` is empty so adapters without history are still valid."""
        del since
        if False:
            yield
