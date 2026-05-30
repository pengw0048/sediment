"""Bitemporal skill edge helpers."""

from __future__ import annotations

from pke.evidence.models import iso_utc
from pke.graph.kuzu_store import KuzuStore


def upsert_relates_to(
    graph: KuzuStore,
    *,
    src: str,
    dst: str,
    relation_type: str,
    strength: float,
    source: str,
    valid_from: str | None = None,
    valid_to: str | None = None,
    recorded_from: str | None = None,
    recorded_to: str | None = None,
) -> None:
    """Write a bitemporal edge with four timestamps."""
    now = iso_utc()
    graph.upsert_edge(
        {
            "src": src,
            "dst": dst,
            "relation_type": relation_type,
            "strength": strength,
            "source": source,
            "valid_from": valid_from or now,
            "valid_to": valid_to,
            "recorded_from": recorded_from or now,
            "recorded_to": recorded_to,
            "created_at": now,
        }
    )
