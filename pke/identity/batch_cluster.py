"""Batch clustering and hierarchy helpers."""

from __future__ import annotations

from collections import defaultdict


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
    """Produce a deterministic connected-component hierarchy."""
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(a: str, b: str) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for src, dst, weight in edges:
        if weight >= 0.3:
            union(src, dst)
    groups: dict[str, list[str]] = defaultdict(list)
    for node in list(parent):
        groups[find(node)].append(node)
    return {root: sorted(nodes) for root, nodes in sorted(groups.items())}
