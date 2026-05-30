"""Anderson-style spreading activation across the skill hierarchy.

When one skill's mastery moves, neighbors connected by parent/child edges
absorb a fraction of the change so retrievability does not jump
discontinuously across a tree that the user actually treats as one body
of knowledge. The fractions ``alpha`` are asymmetric: a parent absorbs
less of a child's swing (default 0.4) than a child inherits from a parent
(default 0.7), matching the intuition that practicing a specific subskill
strengthens the umbrella concept more weakly than the umbrella's mastery
predicts subskill recall.

The function is a pure rewriter over ``new_values`` plus a list of
``(parent_id, child_id)`` edges. The decay job loads edges from Kuzu and
passes them in; tests can call it directly with synthetic edges.
"""

from __future__ import annotations

from pke.db.sqlite import SQLiteStore


def spread_activation(
    sqlite: SQLiteStore,
    *,
    new_values: dict[str, float],
    child_to_parent_alpha: float,
    parent_to_child_alpha: float,
    updated_at: str,
    edges: list[tuple[str, str]] | None = None,
) -> dict[str, float]:
    """Blend retrievabilities across parent/child edges and persist them.

    Args:
        sqlite: store to write the blended values into.
        new_values: mapping ``skill_id -> retrievability`` after decay,
            *before* spreading. Mutated in place with the post-spread
            values for the caller to inspect.
        child_to_parent_alpha: weight applied when pulling child mastery
            into a parent.
        parent_to_child_alpha: weight applied when pulling parent mastery
            into a child.
        updated_at: ISO timestamp stamped onto every row touched.
        edges: list of ``(parent_id, child_id)`` tuples. When ``None`` or
            empty, the function is a no-op and ``new_values`` is returned
            unchanged. Use this hook to plug Kuzu-loaded edges in later
            without changing the decay job.

    Returns the (now-spread) ``new_values`` dict.
    """
    if not edges:
        return new_values

    blended = dict(new_values)
    for parent_id, child_id in edges:
        parent = blended.get(parent_id)
        child = blended.get(child_id)
        if parent is None or child is None:
            continue
        parent_blended = parent + child_to_parent_alpha * (child - parent)
        child_blended = child + parent_to_child_alpha * (parent - child)
        blended[parent_id] = max(0.0, min(1.0, parent_blended))
        blended[child_id] = max(0.0, min(1.0, child_blended))

    changed = [
        (value, updated_at, skill_id)
        for skill_id, value in blended.items()
        if value != new_values.get(skill_id)
    ]
    if changed:
        sqlite.conn.executemany(
            "UPDATE skill_mastery_state "
            "SET unaided_retrievability = ?, updated_at = ? "
            "WHERE skill_id = ?",
            changed,
        )
    new_values.update(blended)
    return new_values
