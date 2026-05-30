"""Async queue bridge from adapters to evidence ingestion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pke.evidence.models import EvidenceEvent


@dataclass(kw_only=True, slots=True)
class EvidenceQueue:
    """Small asyncio queue used by adapter runners."""

    maxsize: int = 1000
    _queue: asyncio.Queue[EvidenceEvent] = field(init=False)

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.maxsize)

    async def put(self, event: EvidenceEvent) -> None:
        """Enqueue an event."""
        await self._queue.put(event)

    async def get(self) -> EvidenceEvent:
        """Dequeue an event."""
        return await self._queue.get()
