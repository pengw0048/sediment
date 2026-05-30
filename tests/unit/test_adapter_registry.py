"""Adapter Protocol coverage tests."""

from __future__ import annotations

import asyncio

from pke.adapters.base import AdapterConfig, AdapterState, InputAdapter
from pke.adapters.registry import ALL_ADAPTERS


def test_every_adapter_class_is_an_input_adapter() -> None:
    """Every registered adapter passes runtime_checkable isinstance(InputAdapter)."""
    assert len(ALL_ADAPTERS) == 10
    for cls in ALL_ADAPTERS:
        instance = cls()
        assert isinstance(instance, InputAdapter), f"{cls.__name__} does not satisfy InputAdapter"


def test_adapter_names_are_unique() -> None:
    """No two adapters share a name. Names are stable ids the daemon stores in state."""
    names = [cls().name for cls in ALL_ADAPTERS]
    assert len(names) == len(set(names))


def test_adapter_lifecycle_round_trip() -> None:
    """Start -> health(running) -> stop -> health(stopped) works for every adapter."""

    async def driver() -> None:
        config = AdapterConfig(enabled=True, source_id="test", options={})
        for cls in ALL_ADAPTERS:
            instance = cls()
            await instance.start(config=config)
            health = await instance.health()
            assert health.state is AdapterState.RUNNING
            await instance.stop()
            health = await instance.health()
            assert health.state is AdapterState.STOPPED

    asyncio.run(driver())


def test_default_events_iterator_is_empty() -> None:
    """The base events() must be an empty async iterator, not crash."""

    async def driver() -> None:
        for cls in ALL_ADAPTERS:
            instance = cls()
            items = []
            async for event in instance.events():
                items.append(event)
            assert items == []

    asyncio.run(driver())


def test_default_backfill_iterator_is_empty() -> None:
    """The base backfill() must yield nothing by default."""

    async def driver() -> None:
        for cls in ALL_ADAPTERS:
            instance = cls()
            items = []
            async for event in instance.backfill(since=None):
                items.append(event)
            assert items == []

    asyncio.run(driver())
