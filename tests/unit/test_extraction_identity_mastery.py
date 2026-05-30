"""Extraction, identity, graph, and mastery tests."""

from pke.adapters.manual_cli import build_manual_event
from pke.extraction.runner import ExtractionRunner
from pke.graph.edges import upsert_relates_to
from pke.graph.kuzu_store import KuzuStore
from pke.identity.ann_index import AnnIndex
from pke.identity.embedder import Embedder
from pke.identity.resolver import IdentityResolver
from pke.mastery.hlr import HLR
from pke.mastery.state import MasteryUpdater
from pke.testing import MockLLMClient


async def test_extraction_persists_candidates(app):
    app.evidence.add(build_manual_event(user="How do I configure FastAPI routes?"))
    count = await ExtractionRunner(sqlite=app.sqlite, client=MockLLMClient()).extract_pending()
    rows = app.sqlite.conn.execute("SELECT * FROM skill_candidates").fetchall()
    assert count == 1
    assert rows
    assert 0 <= rows[0]["confidence"] <= 1


async def test_identity_resolves_to_skill_node(app):
    app.evidence.add(build_manual_event(user="How do I configure FastAPI routes?"))
    await ExtractionRunner(sqlite=app.sqlite, client=MockLLMClient()).extract_pending()
    resolver = IdentityResolver(sqlite=app.sqlite, embedder=Embedder(), ann=AnnIndex())
    decisions = resolver.resolve_pending()
    skills = app.sqlite.conn.execute("SELECT * FROM skill_nodes").fetchall()
    assert decisions
    assert skills


def test_embedder_contract_and_matryoshka():
    embedder = Embedder()
    vector = embedder.embed("nomic embed text")
    rho = embedder.matryoshka_correlation(["espresso grind", "espresso dose", "kubectl pods"])
    assert len(vector) == 768
    assert rho >= 0.8


def test_graph_bitemporal_edges(tmp_path):
    graph = KuzuStore(root=tmp_path / "graph.kuzu")
    graph.ensure_schema()
    upsert_relates_to(
        graph,
        src="a",
        dst="b",
        relation_type="sibling",
        strength=0.8,
        source="unit",
    )
    assert {
        "t_valid_start",
        "t_valid_end",
        "t_observed_start",
        "t_observed_end",
    }.issubset(graph.edges[0])


def test_hlr_formula_and_mastery_update(app):
    skill_id = "skill01"
    now = "2026-05-29T00:00:00.000Z"
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding, first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES (?, 'test skill', '', zeroblob(3072), ?, ?, ?, ?)
        """,
        (skill_id, now, now, now, now),
    )
    app.sqlite.conn.execute(
        "INSERT INTO skill_mastery_state(skill_id, updated_at) VALUES (?, ?)",
        (skill_id, now),
    )
    app.sqlite.conn.commit()
    assert HLR(theta=[0.0]).recall_probability(delta_hours=1.0, features=[1.0]) == 0.5
    MasteryUpdater(sqlite=app.sqlite).update_review(
        skill_id=skill_id,
        grade="pass",
        grader_kind="symbolic",
        item_type="replay_self_try",
    )
    row = app.sqlite.conn.execute(
        "SELECT unaided_retrievability, functional_stability FROM skill_mastery_state WHERE skill_id = ?",
        (skill_id,),
    ).fetchone()
    assert row["unaided_retrievability"] > 0
    assert row["functional_stability"] == 0
