"""Background job scheduler powered by APScheduler.

The daemon owns one :class:`AsyncIOScheduler`. Jobs are registered through
:func:`register_default_jobs`, which is the single source of truth for the
cron / interval table. Adding a new periodic job means adding one entry
there; everything else (signal handling, lifecycle, listing) flows from it.

Jobs themselves live under :mod:`pke.maintenance.jobs`. Each takes the
:class:`pke.app.App` and runs synchronously; the scheduler wraps them in
``asyncio.to_thread`` so a blocking SQL query never stalls the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from pke.maintenance.jobs import (
    audit_merge,
    audit_split,
    decay,
    distill,
    edc,
    reembed,
    vacuum,
)

TriggerSpec: TypeAlias = CronTrigger | IntervalTrigger
JobFn = Callable[[Any], None | int]


@dataclass(frozen=True, kw_only=True, slots=True)
class JobEntry:
    """One scheduled maintenance job."""

    name: str
    trigger: TriggerSpec
    job: JobFn
    description: str = ""


def default_job_entries() -> list[JobEntry]:
    """Canonical maintenance schedule.

    Hours are local time; APScheduler honors the system timezone. Adjust at
    the deployment layer (env ``TZ``) rather than per-job.
    """
    return [
        JobEntry(
            name="vacuum",
            trigger=CronTrigger(hour=2, minute=30),
            job=vacuum.run,
            description="WAL checkpoint + VACUUM the SQLite store.",
        ),
        JobEntry(
            name="decay",
            trigger=CronTrigger(hour=3, minute=0),
            job=decay.run,
            description="Apply a retrievability decay tick to skill mastery state.",
        ),
        JobEntry(
            name="audit_merge",
            trigger=CronTrigger(hour=3, minute=30),
            job=audit_merge.run,
            description="Surface open merge audits to the admin queue.",
        ),
        JobEntry(
            name="audit_split",
            trigger=CronTrigger(hour=3, minute=35),
            job=audit_split.run,
            description="Surface open split audits to the admin queue.",
        ),
        JobEntry(
            name="reembed",
            trigger=CronTrigger(day_of_week="sun", hour=4, minute=30),
            job=reembed.run,
            description="Re-embed skills whose embedding_model_version is stale.",
        ),
        JobEntry(
            name="distill",
            trigger=CronTrigger(day_of_week="sun", hour=5, minute=0),
            job=distill.run,
            description="Update cross-encoder distillation when enough labels accumulate.",
        ),
        JobEntry(
            name="edc",
            trigger=CronTrigger(hour=4, minute=0),
            job=edc.run,
            description="Extract-Define-Canonicalize merge sweep over close skill pairs.",
        ),
    ]


def build_scheduler(app: Any, *, entries: list[JobEntry] | None = None) -> AsyncIOScheduler:
    """Create an :class:`AsyncIOScheduler` with every job in ``entries`` registered.

    The returned scheduler is **not** started. Callers (daemon / lifespan)
    call :meth:`AsyncIOScheduler.start` once the event loop is running.
    """
    scheduler = AsyncIOScheduler()
    register_default_jobs(scheduler, app, entries=entries)
    return scheduler


def register_default_jobs(
    scheduler: AsyncIOScheduler,
    app: Any,
    *,
    entries: list[JobEntry] | None = None,
) -> None:
    """Register every entry from :func:`default_job_entries` (or a custom list)."""
    for entry in entries or default_job_entries():
        scheduler.add_job(
            _wrap(entry.job, app),
            trigger=entry.trigger,
            id=entry.name,
            name=entry.name,
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1,
        )


def _wrap(job: JobFn, app: Any) -> Callable[[], Awaitable[None]]:
    """Wrap a synchronous job in a thread so it never blocks the event loop.

    Jobs whose handler signature accepts the full :class:`App` (e.g. EDC,
    which needs ``app.llm_client``) are passed the App; legacy jobs that
    only need the SQLite store are passed ``app.sqlite``.
    """
    from inspect import signature

    needs_app = "app" in signature(job).parameters or any(
        name == "app" for name in signature(job).parameters
    )

    async def runner() -> None:
        target: Any = app if needs_app else app.sqlite
        await asyncio.to_thread(job, target)

    return runner


async def run_daemon(app: Any, *, stop_event: asyncio.Event | None = None) -> None:
    """Run the scheduler until SIGINT / SIGTERM (or ``stop_event`` fires).

    Used by the ``pke daemon`` CLI entry point. Returns cleanly so callers
    can shut down associated resources (DB connection, etc.).

    Tests pass a ``stop_event`` they own so they can stop the daemon without
    sending real signals to the test process.
    """
    scheduler = build_scheduler(app)
    scheduler.start()
    stop = stop_event or asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Windows event loops do not implement add_signal_handler; fall
            # back to letting the default KeyboardInterrupt propagate.
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)


@dataclass(kw_only=True, slots=True)
class JobScheduler:
    """In-process interval loop used by callers that do not want APScheduler.

    The maintenance daemon and the FastAPI lifespan use
    :func:`build_scheduler` instead. This class is kept for embedding in
    tests or single-shot scripts where bringing up APScheduler is overkill.
    """

    jobs: dict[str, tuple[float, Callable[[], Awaitable[None]]]] = field(default_factory=dict)
    running: bool = False

    def add_interval(
        self, name: str, *, seconds: float, job: Callable[[], Awaitable[None]]
    ) -> None:
        self.jobs[name] = (seconds, job)

    async def run_once(self) -> None:
        for _, job in self.jobs.values():
            await job()

    async def run_forever(self) -> None:
        self.running = True
        while self.running:
            await self.run_once()
            await asyncio.sleep(60)

    def stop(self) -> None:
        self.running = False
