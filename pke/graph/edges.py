"""Bitemporal skill edge helpers.

ARCH-3 invariant: edges are **invalidated** (``t_valid_end`` stamp) rather
than physically deleted. The public API is :func:`upsert_relates_to` and
:func:`invalidate_relates_to`. If a maintenance job ever needs to garbage
collect long-invalidated rows, the deletion lives **inside that job** so a
reviewer sees the intent in one place — there is no general-purpose edge
delete in this module.
"""

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


def invalidate_relates_to(
    graph: KuzuStore,
    *,
    src: str,
    dst: str,
    relation_type: str,
    t_valid_end: str | None = None,
) -> None:
    """Retire an edge by stamping ``t_valid_end``.

    This is the supported way to make an edge stop applying. The row stays
    in the graph (preserving bitemporal history) and queries that filter on
    ``t_valid_end IS NULL OR t_valid_end > now`` skip it.
    """
    graph.upsert_edge(
        {
            "src": src,
            "dst": dst,
            "relation_type": relation_type,
            "t_valid_end": t_valid_end or iso_utc(),
        }
    )
