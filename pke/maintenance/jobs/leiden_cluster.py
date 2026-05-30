"""Weekly Leiden community detection over the skill centroid graph.

Produces a one-level hierarchy: every detected community gets a single
"parent" skill (the one with the highest in-cluster degree, ties broken
by id) and the rest of the cluster becomes its children. The hierarchy
is written as ``parent_of`` edges into the Kuzu graph, which is the
same store ``decay.run`` reads for spreading activation.

Prior ``parent_of`` edges are invalidated (``t_valid_end`` stamped)
before the new ones are upserted so the bitemporal history stays
intact and a downstream consumer can re-derive the previous week's
hierarchy by filtering on ``t_valid_end``.

Single-element communities are skipped — there is no useful "parent"
edge to write when the cluster is a singleton.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pke.evidence.models import iso_utc
from pke.graph.edges import invalidate_relates_to, upsert_relates_to
from pke.identity.batch_cluster import leiden_hierarchy
from pke.identity.embedder import cosine
from pke.identity.resolver import blob_to_vector

_HIERARCHY_RELATION_TYPE = "parent_of"
_EDGE_THRESHOLD = 0.70  # leiden_hierarchy itself drops edges below 0.7
_SOURCE = "leiden_weekly"


def run(app: Any) -> int:
    """Recompute the skill hierarchy and persist it as ``parent_of`` edges.

    Returns the number of ``parent_of`` edges written.
    """
    sqlite = app.sqlite
    graph = getattr(app, "graph", None)
    if graph is None:
        return 0

    rows = sqlite.conn.execute(
        """
        SELECT id, embedding
        FROM skill_nodes
        WHERE user_status = 'active' AND embedding IS NOT NULL
        """
    ).fetchall()
    vectors: dict[str, list[float]] = {}
    for row in rows:
        vec = blob_to_vector(bytes(row["embedding"]) if row["embedding"] else b"")
        if vec:
            vectors[str(row["id"])] = vec
    if len(vectors) < 2:
        return 0

    ids = sorted(vectors)
    edges: list[tuple[str, str, float]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            sim = cosine(vectors[a], vectors[b])
            if sim >= _EDGE_THRESHOLD:
                edges.append((a, b, float(sim)))

    if not edges:
        _invalidate_prior(graph, vectors)
        return 0

    communities = leiden_hierarchy(edges)

    degree: dict[str, int] = defaultdict(int)
    for a, b, _ in edges:
        degree[a] += 1
        degree[b] += 1

    now = iso_utc()
    new_edges: list[tuple[str, str]] = []
    for members in communities.values():
        if len(members) < 2:
            continue
        parent = max(members, key=lambda node: (degree[node], node))
        for child in members:
            if child == parent:
                continue
            new_edges.append((parent, child))

    _invalidate_prior(graph, vectors)
    for parent, child in new_edges:
        upsert_relates_to(
            graph,
            src=parent,
            dst=child,
            relation_type=_HIERARCHY_RELATION_TYPE,
            strength=1.0,
            source=_SOURCE,
            t_valid_start=now,
        )
    return len(new_edges)


def _invalidate_prior(graph: Any, vectors: dict[str, list[float]]) -> None:
    """Retire any open ``parent_of`` edges whose endpoints we still know about."""
    if not vectors:
        return
    now = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        edges = list(graph.edges)
    except Exception:
        return
    for edge in edges:
        if edge.get("relation_type") != _HIERARCHY_RELATION_TYPE:
            continue
        if edge.get("t_valid_end"):
            continue
        src = str(edge.get("src", ""))
        dst = str(edge.get("dst", ""))
        if not src or not dst:
            continue
        invalidate_relates_to(
            graph,
            src=src,
            dst=dst,
            relation_type=_HIERARCHY_RELATION_TYPE,
            t_valid_end=now,
        )


__all__ = ["run"]
