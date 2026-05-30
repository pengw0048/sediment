"""Adapter lifecycle runner.

Owns the start/stop calls for every :class:`InputAdapter` registered in
:data:`pke.adapters.registry.ALL_ADAPTERS` plus the background task that
drains :class:`EvidenceQueue` into the evidence store. ``run_daemon``
calls :func:`start_adapters` once at startup and :func:`stop_adapters`
on shutdown so the daemon's lifetime owns every live tailer / watcher /
HTTP proxy.

Two adapters are real producers today:

* :class:`ClaudeCodeTailerAdapter` boots a :class:`TailWatcher` over
  ``~/.claude/transcripts/`` and converts each new JSONL line to an
  :class:`EvidenceEvent`.
* :class:`FileWatcherAdapter` boots a watchdog observer over
  ``~/PKE/inbox/`` and imports drop-in conversation archives.

The other eight adapters in the registry are still driven by their
free-function call paths (CLI ``pke evidence add``, HTTP proxy handlers,
the browser extension's ``/api/v1/evidence``) — but they participate in
``start_adapters`` so future iterations can move their lifecycle here
without changing the daemon.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pke.adapters.base import AdapterConfig
from pke.evidence.models import EvidenceEvent
from pke.evidence.queue import EvidenceQueue

_DEFAULT_INBOX = Path("~/PKE/inbox/").expanduser()
_DEFAULT_TRANSCRIPTS = Path("~/.claude/transcripts/").expanduser()


@dataclass(kw_only=True, slots=True)
class AdapterRuntime:
    """Live adapter bundle returned by :func:`start_adapters`."""

    queue: EvidenceQueue
    started: list[Any] = field(default_factory=list)
    drainer: asyncio.Task[None] | None = None

    async def stop(self) -> None:
        """Stop every started adapter and the drainer task in reverse order."""
        for adapter in reversed(self.started):
            with contextlib.suppress(Exception):
                await adapter.stop()
        if self.drainer is not None and not self.drainer.done():
            self.drainer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.drainer


async def start_adapters(
    app: Any,
    *,
    transcripts_dir: Path | None = None,
    inbox_dir: Path | None = None,
    drain_handler: Any = None,
) -> AdapterRuntime:
    """Start every adapter in :data:`ALL_ADAPTERS` and the queue drainer.

    Returns an :class:`AdapterRuntime` that the daemon stores until
    shutdown. Adapters that fail to start get logged and skipped — one
    broken adapter must not prevent the others from running.
    """
    from pke.adapters.registry import (
        ALL_ADAPTERS,
        ClaudeCodeTailerAdapter,
        FileWatcherAdapter,
    )

    queue = EvidenceQueue()
    started: list[Any] = []
    config = AdapterConfig(enabled=True, source_id="daemon", options={})

    for cls in ALL_ADAPTERS:
        adapter = cls()
        # Producer adapters need the queue + their target directory threaded
        # in before start; the others get a no-op start from _AdapterBase.
        if isinstance(adapter, ClaudeCodeTailerAdapter):
            adapter.queue = queue
            adapter.directory = transcripts_dir or _DEFAULT_TRANSCRIPTS
        elif isinstance(adapter, FileWatcherAdapter):
            adapter.queue = queue
            adapter.directory = inbox_dir or _DEFAULT_INBOX
        try:
            await adapter.start(config=config)
        except Exception:
            # Continue starting the rest; a broken adapter shouldn't bring
            # the daemon down.
            continue
        started.append(adapter)

    handler = drain_handler or _make_default_drainer(app)
    drainer = asyncio.create_task(_drain_loop(queue, handler))
    return AdapterRuntime(queue=queue, started=started, drainer=drainer)


def _make_default_drainer(app: Any) -> Any:
    """Default queue drainer: hand events to ``app.evidence.add``."""

    async def handler(event: EvidenceEvent) -> None:
        await asyncio.to_thread(app.evidence.add, event)

    return handler


async def _drain_loop(queue: EvidenceQueue, handler: Any) -> None:
    """Pull events off the queue and call ``handler`` until cancelled."""
    while True:
        try:
            event = await queue.get()
        except asyncio.CancelledError:
            raise
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            # Drop the event rather than crashing the drainer; an
            # adapter feeding malformed events should not silence the
            # whole pipeline.
            continue


__all__ = ["AdapterRuntime", "start_adapters"]
