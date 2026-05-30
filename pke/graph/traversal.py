"""Graph traversal helpers used by review generation."""

from __future__ import annotations

from pke.graph.kuzu_store import KuzuStore


def neighborhood(graph: KuzuStore, skill_id: str, *, limit: int = 10) -> list[str]:
    """Return neighboring skill ids."""
    neighbors: list[str] = []
    for edge in graph.neighbors(skill_id):
        other = edge["dst"] if edge.get("src") == skill_id else edge["src"]
        neighbors.append(str(other))
    return neighbors[:limit]
