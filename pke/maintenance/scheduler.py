"""Cron-like background job scheduler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

Job = Callable[[], Awaitable[None]]


@dataclass(kw_only=True, slots=True)
class JobScheduler:
    """Simple interval scheduler used by the local daemon."""

    jobs: dict[str, tuple[float, Job]] = field(default_factory=dict)
    running: bool = False

    def add_interval(self, name: str, *, seconds: float, job: Job) -> None:
        """Register an interval job."""
        self.jobs[name] = (seconds, job)

    async def run_once(self) -> None:
        """Run every registered job once."""
        for _, job in self.jobs.values():
            await job()

    async def run_forever(self) -> None:
        """Run jobs until cancelled."""
        self.running = True
        while self.running:
            await self.run_once()
            await asyncio.sleep(60)

    def stop(self) -> None:
        """Stop the scheduler loop."""
        self.running = False
