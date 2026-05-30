"""Weekly Leiden hierarchy job tests."""

from __future__ import annotations

from pke.evidence.models import iso_utc
from pke.identity.resolver import vector_to_blob
from pke.maintenance.jobs import leiden_cluster


def _insert_active_skill(app, *, skill_id: str, embedding: list[float]) -> None:
    now = iso_utc()
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding, cluster_size,
          first_seen_at, last_seen_at, created_at, updated_at, user_status
        )
        VALUES (?, ?, '', ?, 1, ?, ?, ?, ?, 'active')
        """,
        (
            skill_id,
            skill_id,
            vector_to_blob(embedding),
            now,
            now,
            now,
            now,
        ),
    )
    app.sqlite.conn.commit()


def test_leiden_cluster_writes_parent_of_edges_for_tight_cluster(app) -> None:
    """Skills with high pairwise cosine land in one Leiden community with a parent edge."""
    base = [1.0, 0.1, 0.0]
    for sid in ("a", "b", "c"):
        _insert_active_skill(
            app,
            skill_id=sid,
            embedding=base,
        )

    written = leiden_cluster.run(app)

    assert written == 2, "three near-identical skills should form one community with 2 child edges"
    edges = [
        e
        for e in app.graph.edges
        if e.get("relation_type") == "parent_of" and not e.get("t_valid_end")
    ]
    assert len(edges) == 2
    parents = {e["src"] for e in edges}
    assert len(parents) == 1, "exactly one parent per community"


def test_leiden_cluster_writes_nothing_for_disconnected_singletons(app) -> None:
    """Below-threshold cosines produce no parent_of edges and no errors."""
    _insert_active_skill(app, skill_id="x", embedding=[1.0, 0.0, 0.0])
    _insert_active_skill(app, skill_id="y", embedding=[0.0, 1.0, 0.0])
    _insert_active_skill(app, skill_id="z", embedding=[0.0, 0.0, 1.0])

    written = leiden_cluster.run(app)

    assert written == 0
    open_edges = [
        e
        for e in app.graph.edges
        if e.get("relation_type") == "parent_of" and not e.get("t_valid_end")
    ]
    assert open_edges == []


def test_leiden_cluster_invalidates_prior_edges_before_writing_new(app) -> None:
    """Re-running the job retires last week's parent_of edges before writing new ones."""
    from pke.graph.edges import upsert_relates_to

    # Two stale parent_of edges from a "prior week" that no longer match current
    # skill state.
    _insert_active_skill(app, skill_id="a", embedding=[1.0, 0.0, 0.0])
    _insert_active_skill(app, skill_id="b", embedding=[1.0, 0.0, 0.0])

    upsert_relates_to(
        app.graph,
        src="a",
        dst="b",
        relation_type="parent_of",
        strength=1.0,
        source="prior_week",
    )

    leiden_cluster.run(app)

    edges = [e for e in app.graph.edges if e.get("relation_type") == "parent_of"]
    # The bitemporal history keeps the old row but stamps t_valid_end on it.
    closed = [e for e in edges if e.get("t_valid_end")]
    open_now = [e for e in edges if not e.get("t_valid_end")]
    assert len(closed) >= 1
    assert len(open_now) >= 1


def test_decay_picks_up_leiden_hierarchy_end_to_end(app, monkeypatch) -> None:
    """The decay job loads the parent_of edges the leiden job wrote."""
    from pke.maintenance.jobs import decay as decay_module

    _insert_active_skill(app, skill_id="a", embedding=[1.0, 0.1, 0.0])
    _insert_active_skill(app, skill_id="b", embedding=[1.0, 0.1, 0.0])
    # Mastery rows so decay has something to update.
    for sid in ("a", "b"):
        app.sqlite.conn.execute(
            "INSERT INTO skill_mastery_state(skill_id, unaided_retrievability, unaided_reps, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (sid, 0.5, 0, iso_utc()),
        )
    app.sqlite.conn.commit()

    leiden_cluster.run(app)

    captured: dict[str, list[tuple[str, str]] | None] = {"edges": None}

    def _spy(*args, **kwargs):
        captured["edges"] = kwargs.get("edges")
        from pke.mastery.spreading import spread_activation as real

        return real(*args, **kwargs)

    monkeypatch.setattr(decay_module, "spread_activation", _spy)
    decay_module.run(app)

    assert captured["edges"] is not None
    assert len(captured["edges"]) >= 1


def test_scheduler_registers_leiden_cluster_entry() -> None:
    """leiden_cluster shows up in default_job_entries."""
    from pke.maintenance.scheduler import default_job_entries

    names = {entry.name for entry in default_job_entries()}
    assert "leiden_cluster" in names
