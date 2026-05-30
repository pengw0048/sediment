"""Extraction, identity, graph, and mastery tests."""

import pytest

from pke.adapters.manual_cli import build_manual_event
from pke.extraction.llm_client import LocalClient
from pke.extraction.runner import ExtractionRunner
from pke.graph.edges import upsert_relates_to
from pke.graph.kuzu_store import KuzuStore
from pke.identity.ann_index import AnnIndex
from pke.identity.embedder import Embedder
from pke.identity.resolver import IdentityResolver
from pke.mastery.fsrs import FSRSScheduler
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


def test_fsrs_scheduler_real_state_transitions():
    """B8 follow-up: FSRSScheduler must use the real fsrs library.

    Produces 19-parameter FSRS-4.5 transitions and reports sensible stability behavior.
    """
    scheduler = FSRSScheduler()
    # cold start (no prior state) on a pass should land in learning/review with positive stability
    first = scheduler.schedule(grade="pass", stability=0.0, difficulty=0.0)
    assert first.state in {"learning", "review"}
    assert first.stability > 0
    # a follow-up pass should not collapse stability
    second = scheduler.schedule(
        grade="pass", stability=first.stability, difficulty=first.difficulty
    )
    assert second.stability > 0
    # a fail should never grow stability above the most recent pass
    fail = scheduler.schedule(
        grade="fail", stability=second.stability, difficulty=second.difficulty
    )
    assert fail.stability <= second.stability


def test_local_client_raises_clear_error_when_thinking_disabled(monkeypatch):
    """B15 contract: LocalClient must raise NotImplementedError when thinking is disabled.

    When ``enable_thinking=False`` but llama-cpp-python cannot pass
    ``chat_template_kwargs``, ``LocalClient`` must raise ``NotImplementedError`` with an
    actionable message — NOT silently fall back to thinking-on output.
    """

    class _FakeLlama:
        @staticmethod
        def create_chat_completion(
            messages, temperature, response_format
        ):  # no chat_template_kwargs
            raise AssertionError("should not be invoked when enable_thinking=False is unsupported")

    monkeypatch.setattr(LocalClient, "_llama", lambda self: _FakeLlama())

    import asyncio

    client = LocalClient(enable_thinking=False)
    with pytest.raises(NotImplementedError, match="enable_thinking"):
        asyncio.run(client.complete_json(system="s", user="u"))
