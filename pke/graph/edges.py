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
    t_valid_start: str | None = None,
    t_valid_end: str | None = None,
    t_observed_start: str | None = None,
    t_observed_end: str | None = None,
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
            "t_valid_start": t_valid_start or now,
            "t_valid_end": t_valid_end,
            "t_observed_start": t_observed_start or now,
            "t_observed_end": t_observed_end,
            "created_at": now,
        }
    )
