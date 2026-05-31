"""Concrete :class:`InputAdapter` implementations for every input source.

Each adapter module under :mod:`pke.adapters` exposes functions that
parse or watch one external surface. This module wraps those functions
in a small class that satisfies the :class:`InputAdapter` runtime
protocol — ``name``, ``version``, ``start``, ``stop``, ``events``,
``health``, ``backfill`` — so the daemon can iterate the registry and
treat every adapter the same way regardless of whether it is a
JSONL tailer, a watchdog inbox, a passive HTTP proxy, or a one-shot
archive importer.

``ALL_ADAPTERS`` is the registered list; ``register`` lets test code add
a new adapter without editing this module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from pke.adapters.base import AdapterConfig, AdapterState, InputAdapter, _AdapterBase
from pke.evidence.models import EvidenceEvent


@dataclass(kw_only=True, slots=True)
class AnthropicProxyAdapter(_AdapterBase):
    """Passive HTTP proxy in front of api.anthropic.com.

    Driven via the ``pke proxy anthropic`` CLI command, which calls
    :func:`pke.adapters.anthropic_proxy.create_proxy_app` to build a
    FastAPI app and serves it via uvicorn. The FastAPI app forwards
    every request to ``api.anthropic.com`` verbatim and streams the
    SSE response back to the client chunk by chunk, capturing a
    buffered copy for evidence in a background task. The registry
    entry exists so admin surfaces can list the proxy as a known
    source even when the standalone uvicorn process is not running.
    """

    name: str = "anthropic_proxy"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class OpenAIProxyAdapter(_AdapterBase):
    """Passive HTTP proxy in front of api.openai.com and compatible servers.

    Driven via the ``pke proxy openai`` CLI command. See
    :class:`AnthropicProxyAdapter` for the overall shape — the OpenAI
    proxy adds capture for both ``/v1/chat/completions`` (chunked
    ``data: {…}`` events terminated by ``[DONE]``) and ``/v1/responses``
    (``response.output_text.delta`` events).
    """

    name: str = "openai_proxy"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class BrowserExtensionAdapter(_AdapterBase):
    """FastAPI endpoint receiving events from the MV3 browser extension."""

    name: str = "browser_ext"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class ChatGPTHistoryAdapter(_AdapterBase):
    """One-shot importer for ChatGPT export archives."""

    name: str = "chatgpt_history"
    version: str = "0.1.0"

    async def backfill(self, *, since: float | None = None) -> AsyncIterator[EvidenceEvent]:
        """Importer is invoked from the CLI; this stays empty by default."""
        del since
        if False:
            yield


@dataclass(kw_only=True, slots=True)
class ClaudeAIHistoryAdapter(_AdapterBase):
    """One-shot importer for claude.ai export archives."""

    name: str = "claude_ai_history"
    version: str = "0.1.0"

    async def backfill(self, *, since: float | None = None) -> AsyncIterator[EvidenceEvent]:
        del since
        if False:
            yield


@dataclass(kw_only=True, slots=True)
class ClaudeCodeHookAdapter(_AdapterBase):
    """Receives JSON envelopes from the Claude Code hook installer."""

    name: str = "claude_code_hook"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class ClaudeCodeTailerAdapter(_AdapterBase):
    """Tails ~/.claude/transcripts/*.jsonl via watchdog.

    ``start()`` brings up a :class:`TailWatcher` over the configured
    directory; the handler converts each newly-appended JSONL line into
    an :class:`EvidenceEvent` via
    :func:`pke.adapters.claude_code_tailer.event_from_jsonl_message` and
    pushes it onto :attr:`queue`. Resume offsets are persisted by the
    underlying watcher so a daemon restart picks up where it left off.
    """

    name: str = "claude_code_tailer"
    version: str = "0.1.0"
    queue: object = None  # EvidenceQueue, threaded in by start_adapters
    directory: object = None  # Path, threaded in by start_adapters
    _watcher: object = None

    async def start(self, *, config: AdapterConfig) -> None:
        """Boot a TailWatcher over ``self.directory`` if it's set."""
        del config
        if self.queue is None or self.directory is None:
            self._state = AdapterState.DEGRADED
            self._detail = "queue/directory not configured"
            return
        from pathlib import Path

        from pke.adapters.claude_code_tailer import event_from_jsonl_message
        from pke.adapters.tail_watcher import TailWatcher

        loop = __import__("asyncio").get_running_loop()
        queue = self.queue
        json_mod = __import__("json")

        def handler(event: object) -> None:
            try:
                payload = json_mod.loads(event.raw_line)
            except (json_mod.JSONDecodeError, AttributeError):
                return
            if not isinstance(payload, dict):
                return
            evidence = event_from_jsonl_message(
                Path(str(event.path)), int(event.line_number), payload
            )
            if evidence is None:
                return
            loop.call_soon_threadsafe(
                lambda: __import__("asyncio").ensure_future(queue.put(evidence))
            )

        directory = Path(str(self.directory))
        watcher = TailWatcher(directory, handler=handler)
        watcher.start()
        self._watcher = watcher
        self._state = AdapterState.RUNNING

    async def stop(self) -> None:
        watcher = self._watcher
        if watcher is not None:
            watcher.stop()
            self._watcher = None
        self._state = AdapterState.STOPPED


@dataclass(kw_only=True, slots=True)
class CursorAdapter(_AdapterBase):
    """Reads Cursor's local transcript files."""

    name: str = "cursor"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class FileWatcherAdapter(_AdapterBase):
    """Drop-in inbox importer at ~/PKE/inbox/.

    ``start()`` runs an initial :func:`process_inbox_once` pass so files
    that landed before the daemon was up don't sit in the inbox forever,
    then registers a watchdog observer that re-scans on every new file.
    """

    name: str = "file_watcher"
    version: str = "0.1.0"
    queue: object = None  # EvidenceQueue
    directory: object = None  # Path
    _observer: object = None
    _loop: object = None  # asyncio.AbstractEventLoop captured at start()

    async def start(self, *, config: AdapterConfig) -> None:
        del config
        if self.queue is None or self.directory is None:
            self._state = AdapterState.DEGRADED
            self._detail = "queue/directory not configured"
            return
        import asyncio
        from pathlib import Path

        directory = Path(str(self.directory))
        directory.mkdir(parents=True, exist_ok=True)
        # Capture the asyncio loop while we're definitely on it, so the
        # watchdog thread can safely hand work back across the boundary
        # without depending on the deprecated get_event_loop fallback.
        self._loop = asyncio.get_running_loop()
        await self._drain_now(directory)

        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        outer = self

        class _Handler(FileSystemEventHandler):
            def on_created(self, event: object) -> None:
                if getattr(event, "is_directory", False):
                    return
                outer._schedule_drain(directory)

            on_modified = on_created

        self._observer = Observer()
        self._observer.schedule(  # type: ignore[no-untyped-call]
            _Handler(), str(directory), recursive=False
        )
        self._observer.start()  # type: ignore[no-untyped-call]
        self._state = AdapterState.RUNNING

    async def stop(self) -> None:
        observer = self._observer
        if observer is not None:
            observer.stop()
            observer.join(timeout=5.0)
            self._observer = None
        self._loop = None
        self._state = AdapterState.STOPPED

    def _schedule_drain(self, directory: object) -> None:
        import asyncio

        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._drain_now(directory)))

    async def _drain_now(self, directory: object) -> None:
        """Drain the inbox: parse archives, move files, push events to the queue."""
        import asyncio
        from pathlib import Path

        from pke.adapters.file_watcher import process_inbox_once

        results = await asyncio.to_thread(process_inbox_once, Path(str(directory)))
        for result in results:
            for event in result.events:
                await self.queue.put(event)
                self._events_emitted += 1


@dataclass(kw_only=True, slots=True)
class ManualCLIAdapter(_AdapterBase):
    """`pke evidence add` manual entry."""

    name: str = "manual_cli"
    version: str = "0.1.0"


ALL_ADAPTERS: list[type] = [
    AnthropicProxyAdapter,
    OpenAIProxyAdapter,
    BrowserExtensionAdapter,
    ChatGPTHistoryAdapter,
    ClaudeAIHistoryAdapter,
    ClaudeCodeHookAdapter,
    ClaudeCodeTailerAdapter,
    CursorAdapter,
    FileWatcherAdapter,
    ManualCLIAdapter,
]
"""Every concrete adapter, used by the registry test to enforce coverage."""

ACTIVE_PRODUCERS: list[type] = [
    ClaudeCodeTailerAdapter,
    FileWatcherAdapter,
]
"""Adapters the daemon actually boots into running producers.

Other adapters in :data:`ALL_ADAPTERS` are still driven by their
free-function call paths (CLI ``pke evidence add``, HTTP proxy handlers,
the browser extension's ``/api/v1/evidence``). Their presence in
``ALL_ADAPTERS`` is documentation of the surface this codebase intends
to cover — calling :func:`pke.adapters.runner.start_adapters` does NOT
turn them into producers. Move an entry here only after its ``start()``
becomes a real wire-up.
"""


def register(adapter_cls: type, *, active: bool = False) -> None:
    """Append ``adapter_cls`` to the registry list.

    Set ``active=True`` to also enroll the class in
    :data:`ACTIVE_PRODUCERS` so the daemon will call ``start()`` on it.
    """
    ALL_ADAPTERS.append(adapter_cls)
    if active:
        ACTIVE_PRODUCERS.append(adapter_cls)


__all__ = [
    "ACTIVE_PRODUCERS",
    "ALL_ADAPTERS",
    "AdapterConfig",
    "AdapterState",
    "AnthropicProxyAdapter",
    "BrowserExtensionAdapter",
    "ChatGPTHistoryAdapter",
    "ClaudeAIHistoryAdapter",
    "ClaudeCodeHookAdapter",
    "ClaudeCodeTailerAdapter",
    "CursorAdapter",
    "FileWatcherAdapter",
    "InputAdapter",
    "ManualCLIAdapter",
    "OpenAIProxyAdapter",
    "register",
]
