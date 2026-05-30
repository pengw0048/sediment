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
from pke.review.grader import LLM_JUDGE_MIN_CONFIDENCE, Grader
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
    # halflife = exp(0) = 1h, so p(delta=1h) = 2^-1 = 0.5
    assert HLR(theta=[0.0]).recall_probability(delta_hours=1.0, features=[1.0]) == 0.5
    # Default theta produces ~24h halflife at zero features (only bias=1)
    default_hlr = HLR()
    halflife = default_hlr.halflife([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert 20.0 < halflife < 30.0
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
    """FSRSScheduler uses the real fsrs library and produces sane stability transitions."""
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


async def test_local_client_raises_clear_error_when_thinking_disabled(monkeypatch):
    """LocalClient must raise NotImplementedError when thinking is disabled.

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

    client = LocalClient(enable_thinking=False)
    with pytest.raises(NotImplementedError, match="enable_thinking"):
        await client.complete_json(system="s", user="u")


async def test_grade_llm_judge_uses_rubric_and_returns_confidence():
    """Grader.grade_llm_judge calls the LLM with a rubric.

    Parses {grade, confidence, feedback}, and surfaces low-confidence calls.
    """
    grader = Grader(client=MockLLMClient())
    rubric = {
        "pass": "names a concrete first check",
        "partial": "vague answer with no specifics",
        "fail": "unrelated or empty",
    }
    result = await grader.grade_llm_judge(
        item_prompt="For git rebase --interactive, what's the first concrete thing you would check?",
        user_answer="I check whether the working tree is clean — `git status` first.",
        rubric=rubric,
    )
    assert result.grade in {"pass", "partial", "fail"}
    assert result.confidence > 0
    assert result.feedback


async def test_grade_llm_judge_falls_back_when_confidence_low():
    """Grader flags low-confidence judge calls for self-report fallback.

    A judge confidence below LLM_JUDGE_MIN_CONFIDENCE prefixes feedback
    with [low-confidence judge] so callers can degrade to self-report.
    """

    class _LowConfidenceClient:
        async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
            del system, user
            return {"grade": "partial", "confidence": 0.3, "feedback": "borderline"}

    grader = Grader(client=_LowConfidenceClient())
    result = await grader.grade_llm_judge(
        item_prompt="x", user_answer="y", rubric={"pass": "", "partial": "", "fail": ""}
    )
    assert result.confidence < LLM_JUDGE_MIN_CONFIDENCE
    assert "low-confidence" in result.feedback


async def test_grade_llm_judge_raises_when_no_client():
    """Grader.grade_llm_judge raises when no client is configured."""
    grader = Grader(client=None)
    with pytest.raises(RuntimeError, match="LLMClient"):
        await grader.grade_llm_judge(
            item_prompt="x", user_answer="y", rubric={"pass": "", "partial": "", "fail": ""}
        )


def test_resolver_decide_auto_merges_above_threshold():
    """IdentityResolver auto-merges when cosine >= merge_threshold (0.92)."""
    from unittest.mock import Mock

    from pke.identity.resolver import IdentityResolver

    resolver = IdentityResolver(sqlite=Mock(), embedder=Mock(), ann=Mock(), judge_client=None)
    action, skill_id, judged = resolver._decide(
        candidate_name="x",
        candidate_desc="",
        vector=[0.1, 0.2],
        nearest_id="skill-A",
        nearest_sim=0.95,
        candidate_id="cand-1",
    )
    assert (action, skill_id, judged) == ("merge", "skill-A", False)


def test_resolver_decide_creates_new_below_gray_lower(monkeypatch):
    """IdentityResolver creates a new skill when cosine <= gray_lower (0.78)."""
    from unittest.mock import Mock

    from pke.identity.resolver import IdentityResolver

    monkeypatch.setattr(
        IdentityResolver, "_create_skill", lambda self, name, description, vector: "skill-NEW"
    )
    resolver = IdentityResolver(sqlite=Mock(), embedder=Mock(), ann=Mock(), judge_client=None)
    action, skill_id, judged = resolver._decide(
        candidate_name="x",
        candidate_desc="",
        vector=[0.1, 0.2],
        nearest_id="skill-A",
        nearest_sim=0.55,
        candidate_id="cand-1",
    )
    assert (action, skill_id, judged) == ("new", "skill-NEW", False)


def test_resolver_decide_calls_judge_in_gray_band(monkeypatch):
    """IdentityResolver consults the LLM judge in the gray band and acts on the verdict."""
    from unittest.mock import Mock

    from pke.identity.resolver import GrayBandVerdict, IdentityResolver

    class _JudgeClient:
        async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
            del system, user
            return {"verdict": "merge", "confidence": 0.85, "rationale": "same"}

    monkeypatch.setattr(
        IdentityResolver,
        "_call_gray_band_judge",
        lambda self, **kw: GrayBandVerdict(verdict="merge", confidence=0.85, rationale="same"),
    )
    resolver = IdentityResolver(
        sqlite=Mock(), embedder=Mock(), ann=Mock(), judge_client=_JudgeClient()
    )
    action, skill_id, judged = resolver._decide(
        candidate_name="x",
        candidate_desc="",
        vector=[0.1, 0.2],
        nearest_id="skill-A",
        nearest_sim=0.85,
        candidate_id="cand-1",
    )
    assert action == "merge"
    assert skill_id == "skill-A"
    assert judged is True


def test_resolver_pending_verdict_writes_audit(app):
    """IdentityResolver queues a candidate_review audit on a 'pending' judge verdict."""
    from pke.identity.resolver import GrayBandVerdict, IdentityResolver

    resolver = IdentityResolver(sqlite=app.sqlite, embedder=Embedder(), ann=AnnIndex())
    resolver._enqueue_audit(
        candidate_id="cand-1",
        candidate_name="ambiguous skill",
        existing_skill_id="skill-A",
        cosine=0.85,
        verdict=GrayBandVerdict(verdict="pending", confidence=0.55, rationale="ambiguous"),
    )
    app.sqlite.conn.commit()
    audits = app.sqlite.conn.execute(
        "SELECT audit_type, payload_json FROM pending_audits WHERE resolved_at IS NULL"
    ).fetchall()
    assert any(row["audit_type"] == "candidate_review" for row in audits)


def test_resolver_decide_legacy_path_without_judge(monkeypatch):
    """IdentityResolver without a judge client uses the single-threshold fallback."""
    from unittest.mock import Mock

    from pke.identity.resolver import IdentityResolver

    monkeypatch.setattr(
        IdentityResolver, "_create_skill", lambda self, name, description, vector: "skill-NEW"
    )
    resolver = IdentityResolver(sqlite=Mock(), embedder=Mock(), ann=Mock(), judge_client=None)
    action_hi, sid_hi, _ = resolver._decide(
        candidate_name="x",
        candidate_desc="",
        vector=[0.1],
        nearest_id="skill-A",
        nearest_sim=0.88,
        candidate_id="cand-1",
    )
    action_lo, sid_lo, _ = resolver._decide(
        candidate_name="x",
        candidate_desc="",
        vector=[0.1],
        nearest_id="skill-A",
        nearest_sim=0.82,
        candidate_id="cand-2",
    )
    assert (action_hi, sid_hi) == ("merge", "skill-A")
    assert (action_lo, sid_lo) == ("new", "skill-NEW")


def test_hlr_fit_reduces_loss_on_synthetic_recall_data():
    """HLR.fit optimises theta against a small synthetic dataset."""
    import math
    import random

    from pke.mastery.hlr import HLR

    rng = random.Random(1234)
    # Generate samples whose true halflife is 48h for "well-learned" features
    # (recent_pass_rate=1.0, log_reps high) and 6h for "fresh" features.
    samples: list[tuple[list[float], float, bool]] = []
    for _ in range(200):
        is_well_learned = rng.random() < 0.5
        features = [
            1.0,  # bias
            math.log(20.0 if is_well_learned else 1.0),
            1.0 if is_well_learned else 0.0,
            math.log(20.0 if is_well_learned else 1.0),
            math.log(5.0 if is_well_learned else 0.0 + 1.0),
            3.0 if is_well_learned else 7.0,
            0.0,
            0.0,
        ]
        true_h = 48.0 if is_well_learned else 6.0
        delta = rng.uniform(0.5, 24.0)
        true_p = 2 ** (-delta / true_h)
        recalled = rng.random() < true_p
        samples.append((features, delta, recalled))

    model = HLR()
    initial_predictions = [
        model.recall_probability(delta_hours=delta, features=feats) for feats, delta, _ in samples
    ]
    initial_loss = sum(
        -(1 if rec else 0) * math.log(max(p, 1e-9)) - (0 if rec else 1) * math.log(max(1 - p, 1e-9))
        for (_, _, rec), p in zip(samples, initial_predictions, strict=True)
    ) / len(samples)

    model.fit(samples, max_iter=400)
    fitted_predictions = [
        model.recall_probability(delta_hours=delta, features=feats) for feats, delta, _ in samples
    ]
    fitted_loss = sum(
        -(1 if rec else 0) * math.log(max(p, 1e-9)) - (0 if rec else 1) * math.log(max(1 - p, 1e-9))
        for (_, _, rec), p in zip(samples, fitted_predictions, strict=True)
    ) / len(samples)

    assert fitted_loss < initial_loss


def test_hlr_extract_features_emits_canonical_layout():
    """extract_features returns a vector matching the FEATURE_NAMES order."""
    from pke.mastery.hlr import FEATURE_NAMES, extract_features

    row = {
        "unaided_reps": 5,
        "unaided_stability": 12.0,
        "unaided_difficulty": 4.0,
        "functional_reps": 0,
        "functional_stability": 0.0,
        "functional_difficulty": 0.0,
    }
    vec = extract_features(row, dimension="unaided", has_parent=True, recent_pass_rate=0.8)
    assert len(vec) == len(FEATURE_NAMES)
    # bias must be 1
    assert vec[0] == 1.0
    # is_functional must be 0 for the unaided dimension
    assert vec[6] == 0.0
    # has_parent must be 1
    assert vec[7] == 1.0


async def test_resolver_writes_micro_cluster_id_when_online_clusterer_provided(app):
    """IdentityResolver writes DenStream's cluster id to skill_candidates."""
    from pke.identity.denstream_online import OnlineClusterer

    app.evidence.add(build_manual_event(user="How do I configure FastAPI routes?"))
    await ExtractionRunner(sqlite=app.sqlite, client=MockLLMClient()).extract_pending()
    resolver = IdentityResolver(
        sqlite=app.sqlite,
        embedder=Embedder(),
        ann=AnnIndex(),
        online_clusterer=OnlineClusterer(),
    )
    decisions = resolver.resolve_pending()
    assert decisions
    rows = app.sqlite.conn.execute("SELECT micro_cluster_id FROM skill_candidates").fetchall()
    assert rows
    # DenStream returns int cluster ids (>= 0) once it has seen enough samples;
    # for a single sample the id may be -1 which is the river "unassigned"
    # sentinel. Either way the column must not be NULL when an OnlineClusterer
    # is wired in.
    assert all(row["micro_cluster_id"] is not None for row in rows)
