"""Adapter runner tests: daemon iterates ACTIVE_PRODUCERS through start_adapters."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from pke.adapters.base import AdapterState
from pke.adapters.registry import ALL_ADAPTERS
from pke.adapters.runner import start_adapters


@pytest.mark.asyncio
async def test_start_adapters_only_boots_active_producers(app, tmp_path: Path) -> None:
    """Only the entries in ACTIVE_PRODUCERS boot.

    Passive adapters (proxy, hook receiver, history importers) are
    documentation of the surface; the daemon never start()s them.
    """
    runtime = await start_adapters(
        app,
        transcripts_dir=tmp_path / "transcripts",
        inbox_dir=tmp_path / "inbox",
    )
    try:
        started_names = {a.name for a in runtime.started}
        assert started_names == {
            "claude_code_tailer",
            "file_watcher",
        }, "only the active producers should boot; passive adapters stay out of the daemon path"
        for adapter in runtime.started:
            health = await adapter.health()
            assert health.state is AdapterState.RUNNING
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
        while not captured and time.monotonic() < deadline:  # noqa: ASYNC110
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
        while not captured and time.monotonic() < deadline:  # noqa: ASYNC110
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


@pytest.mark.asyncio
async def test_file_watcher_adapter_pushes_archive_events_to_queue(app, tmp_path: Path) -> None:
    """An inbox archive lands as EvidenceEvent rows on the queue."""
    import json as _json

    captured: list = []

    async def drain(event) -> None:
        captured.append(event)

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive = inbox / "chatgpt_export.json"
    archive.write_text(
        _json.dumps(
            [
                {
                    "id": "conv-1",
                    "title": "test",
                    "create_time": 1717000000.0,
                    "current_node": "node-2",
                    "mapping": {
                        "node-1": {
                            "id": "node-1",
                            "parent": None,
                            "children": ["node-2"],
                            "message": {
                                "id": "node-1",
                                "author": {"role": "user"},
                                "content": {
                                    "content_type": "text",
                                    "parts": ["how do i configure FastAPI?"],
                                },
                                "create_time": 1717000000.0,
                            },
                        },
                        "node-2": {
                            "id": "node-2",
                            "parent": "node-1",
                            "children": [],
                            "message": {
                                "id": "node-2",
                                "author": {"role": "assistant"},
                                "content": {
                                    "content_type": "text",
                                    "parts": ["Use Depends()."],
                                },
                                "create_time": 1717000001.0,
                            },
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    runtime = await start_adapters(
        app,
        transcripts_dir=tmp_path / "transcripts",
        inbox_dir=inbox,
        drain_handler=drain,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not captured and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        assert captured, "FileWatcherAdapter must push archive events to the queue"
        assert any("FastAPI" in turn.content for ev in captured for turn in ev.turns)
        assert (
            inbox / "processed" / "chatgpt_export.json"
        ).exists(), "archive should still be moved to processed/ after a successful drain"
    finally:
        await runtime.stop()


def test_every_registry_entry_satisfies_input_adapter_protocol() -> None:
    """Every ALL_ADAPTERS entry passes isinstance(InputAdapter) at runtime."""
    from pke.adapters.base import InputAdapter

    for cls in ALL_ADAPTERS:
        assert isinstance(cls(), InputAdapter)
