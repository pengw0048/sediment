"""Weekly topic hierarchy discovery."""

from __future__ import annotations

from pke.identity.batch_cluster import leiden_hierarchy


def weekly_hierarchy(edges: list[tuple[str, str, float]]) -> dict[str, list[str]]:
    """Return an explainable hierarchy over centroid graph edges."""
    return leiden_hierarchy(edges)
