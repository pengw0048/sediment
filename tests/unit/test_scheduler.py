"""Tests for the maintenance scheduler (B13)."""

from __future__ import annotations

import asyncio

import pytest
from apscheduler.triggers.cron import CronTrigger

from pke.maintenance.scheduler import (
    JobEntry,
    build_scheduler,
    default_job_entries,
    register_default_jobs,
    run_daemon,
)


def test_default_entries_cover_canonical_jobs():
    """B13 contract: the canonical schedule must include vacuum, decay, audit_*, reembed, distill."""
    entries = default_job_entries()
    names = {entry.name for entry in entries}
    assert {"vacuum", "decay", "audit_merge", "audit_split", "reembed", "distill"}.issubset(names)


def test_every_default_entry_has_cron_trigger():
    """B13 contract: default entries must all be cron-triggered (no interval drift surprises)."""
    for entry in default_job_entries():
        assert isinstance(entry.trigger, CronTrigger), f"{entry.name} is not cron-triggered"


def test_build_scheduler_registers_all_entries(app):
    """B13 contract: build_scheduler wires every entry with a unique job id."""
    scheduler = build_scheduler(app)
    try:
        registered = {job.id for job in scheduler.get_jobs()}
        expected = {entry.name for entry in default_job_entries()}
        assert registered == expected
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def test_register_default_jobs_accepts_custom_entries(app):
    """B13 contract: callers can override the schedule (used by tests + future per-user config)."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    custom = [
        JobEntry(
            name="custom",
            trigger=CronTrigger(hour=1),
            job=lambda sqlite: None,
            description="test-only",
        )
    ]
    scheduler = AsyncIOScheduler()
    try:
        register_default_jobs(scheduler, app, entries=custom)
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "custom"
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def test_run_daemon_returns_when_event_set(app):
    """B13 contract: run_daemon should exit cleanly when its stop event is set.

    Passes a caller-owned stop event so the daemon can be torn down without
    sending real signals to the test process (which would race with pytest's
    own signal handling).
    """

    async def driver() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, stop.set)
        await run_daemon(app, stop_event=stop)

    try:
        asyncio.run(asyncio.wait_for(driver(), timeout=5.0))
    except TimeoutError as exc:
        pytest.fail(f"run_daemon did not return within 5s: {exc!r}")
