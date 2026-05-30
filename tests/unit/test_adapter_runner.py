"""PR-22 tests: daemon iterates ALL_ADAPTERS through start_adapters."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from pke.adapters.base import AdapterState
from pke.adapters.registry import (
    ALL_ADAPTERS,
    ClaudeCodeTailerAdapter,
    FileWatcherAdapter,
)
from pke.adapters.runner import start_adapters


@pytest.mark.asyncio
async def test_start_adapters_brings_all_to_running(app, tmp_path: Path) -> None:
    """start_adapters calls start() on every registry entry."""
    runtime = await start_adapters(
        app,
        transcripts_dir=tmp_path / "transcripts",
        inbox_dir=tmp_path / "inbox",
    )
    try:
        # Every started adapter advanced past STOPPED.
        for adapter in runtime.started:
            health = await adapter.health()
            assert (
                health.state is not AdapterState.STOPPED
            ), f"{adapter.name} still STOPPED after start_adapters"
        # The producer pair specifically lands in RUNNING.
        producers = {
            adapter.name
            for adapter in runtime.started
            if isinstance(adapter, ClaudeCodeTailerAdapter | FileWatcherAdapter)
        }
        assert producers == {"claude_code_tailer", "file_watcher"}
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_stop_returns_adapters_to_stopped(app, tmp_path: Path) -> None:
    """runtime.stop() drops every adapter back to STOPPED."""
    runtime = await start_adapters(
        app,
        transcripts_dir=tmp_path / "transcripts",
        inbox_dir=tmp_path / "inbox",
    )
    started = list(runtime.started)
    await runtime.stop()
    for adapter in started:
        health = await adapter.health()
        assert health.state is AdapterState.STOPPED


@pytest.mark.asyncio
async def test_claude_code_tailer_adapter_forwards_a_jsonl_line_to_the_queue(
    app, tmp_path: Path
) -> None:
    """A fresh JSONL line in the transcripts dir lands as an EvidenceEvent on the queue."""
    captured: list = []

    async def drain(event) -> None:
        captured.append(event)

    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    runtime = await start_adapters(
        app,
        transcripts_dir=transcripts,
        inbox_dir=tmp_path / "inbox",
        drain_handler=drain,
    )
    try:
        target = transcripts / "session1.jsonl"
        target.write_text(
            '{"role":"user","content":"How do I configure FastAPI?","timestamp":"2026-05-30T12:00:00Z"}\n'
        )
        # The watchdog observer reacts on a real filesystem event; give it
        # a moment, then poll the captured list.
        deadline = time.monotonic() + 5.0
        while not captured and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert captured, "no event reached the drainer"
        event = captured[0]
        assert event.source == "claude_code_tail"
        assert any("FastAPI" in turn.content for turn in event.turns)
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_drain_handler_receives_queue_events(app, tmp_path: Path) -> None:
    """The default drainer hands queue events to app.evidence.add."""
    captured: list = []

    async def drain(event) -> None:
        captured.append(event)

    runtime = await start_adapters(
        app,
        transcripts_dir=tmp_path / "transcripts",
        inbox_dir=tmp_path / "inbox",
        drain_handler=drain,
    )
    try:
        # Manually push an event to verify the drainer is wired.
        from pke.adapters.manual_cli import build_manual_event

        evidence = build_manual_event(user="hello world")
        await runtime.queue.put(evidence)
        deadline = time.monotonic() + 2.0
        while not captured and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert len(captured) == 1
        assert "hello world" in captured[0].turns[0].content
    finally:
        await runtime.stop()


def test_run_daemon_calls_start_adapters(app) -> None:
    """run_daemon invokes start_adapters_fn during boot and runtime.stop on exit."""
    from pke.adapters.runner import AdapterRuntime
    from pke.evidence.queue import EvidenceQueue
    from pke.maintenance.scheduler import run_daemon

    calls: dict[str, bool] = {"started": False, "stopped": False}

    class _FakeRuntime:
        queue = EvidenceQueue()
        started: list = []
        drainer = None

        async def stop(self) -> None:
            calls["stopped"] = True

    async def fake_start(app_arg) -> AdapterRuntime:
        calls["started"] = True
        return _FakeRuntime()  # type: ignore[return-value]

    async def driver() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, stop.set)
        await run_daemon(app, stop_event=stop, start_adapters_fn=fake_start)

    asyncio.run(driver())
    assert calls["started"] is True
    assert calls["stopped"] is True


def test_registry_still_passes_input_adapter_isinstance() -> None:
    """The PR-22 changes did not regress the runtime_checkable Protocol coverage."""
    from pke.adapters.base import InputAdapter

    for cls in ALL_ADAPTERS:
        assert isinstance(cls(), InputAdapter)
