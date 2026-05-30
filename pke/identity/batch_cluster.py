"""Batch clustering and hierarchy helpers."""

from __future__ import annotations

from collections import defaultdict

import igraph as ig


def adjusted_rand_index(labels_a: list[int], labels_b: list[int]) -> float:
    """Compute a compact Adjusted Rand Index implementation."""
    if len(labels_a) != len(labels_b):
        raise ValueError("label lists must have the same length")
    n = len(labels_a)
    if n < 2:
        return 1.0
    table: dict[tuple[int, int], int] = defaultdict(int)
    count_a: dict[int, int] = defaultdict(int)
    count_b: dict[int, int] = defaultdict(int)
    for a, b in zip(labels_a, labels_b, strict=True):
        table[(a, b)] += 1
        count_a[a] += 1
        count_b[b] += 1

    def choose2(value: int) -> float:
        return value * (value - 1) / 2

    sum_table = sum(choose2(value) for value in table.values())
    sum_a = sum(choose2(value) for value in count_a.values())
    sum_b = sum(choose2(value) for value in count_b.values())
    total = choose2(n)
    expected = sum_a * sum_b / total if total else 0.0
    maximum = (sum_a + sum_b) / 2
    if maximum == expected:
        return 1.0
    return (sum_table - expected) / (maximum - expected)


def leiden_hierarchy(edges: list[tuple[str, str, float]]) -> dict[str, list[str]]:
    """Cluster centroid graph with igraph Leiden over cosine-weighted edges."""
    nodes = sorted({node for edge in edges for node in edge[:2]})
    if not nodes:
        return {}
    graph = ig.Graph()
    graph.add_vertices(nodes)
    weighted_edges = [(src, dst, weight) for src, dst, weight in edges if weight > 0.7]
    if weighted_edges:
        graph.add_edges([(src, dst) for src, dst, _ in weighted_edges])
        graph.es["weight"] = [weight for _, _, weight in weighted_edges]
        partition = graph.community_leiden(
            weights="weight",
            resolution=1.0,
            n_iterations=10,
        )
        memberships = partition.membership
    else:
        memberships = list(range(len(nodes)))
    groups: dict[int, list[str]] = defaultdict(list)
    for vertex, cluster_id in zip(graph.vs, memberships, strict=True):
        groups[int(cluster_id)].append(str(vertex["name"]))
    return {min(members): sorted(members) for members in groups.values()}
