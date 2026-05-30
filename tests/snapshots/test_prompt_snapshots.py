"""Snapshot-style prompt checks.

These tests guard the LLM prompt templates against silent edits. The contract
they enforce:

1. The frozen polarity vocabulary appears in the system prompt (no drift
   without a deliberate schema migration).
2. The user-side template renders evidence as-is without escaping or
   reformatting (so token offsets in the response remain meaningful).
3. The full rendered system prompt matches a golden file. Regenerate the
   golden file with ``pytest --snapshot-update`` after intentionally editing
   the template.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pke.extraction.prompts import render

_GOLDEN_DIR = Path(__file__).parent / "golden"
_GOLDEN_DIR.mkdir(exist_ok=True)


def test_extraction_system_prompt_contains_frozen_schema_terms() -> None:
    system = render("extract_skills.system.j2")
    for polarity in ("demonstrated", "attempted", "failed", "asked-about"):
        assert polarity in system, f"polarity {polarity!r} missing from system prompt"
    # the JSON output schema MUST mention these field names — parser depends on them
    for field_name in ("rationale", "skills", "name", "polarity", "confidence", "span_start"):
        assert field_name in system, f"output schema field {field_name!r} missing"


def test_extraction_user_prompt_passes_evidence_through_verbatim() -> None:
    evidence = "How do I get the current user inside a FastAPI dependency?"
    rendered = render("extract_skills.user.j2", evidence_text=evidence)
    assert evidence in rendered


def test_extraction_system_prompt_matches_golden() -> None:
    _check_snapshot("extract_skills.system.j2", _GOLDEN_DIR / "extract_skills.system.txt")


def test_judge_answer_system_prompt_contains_frozen_terms() -> None:
    system = render("judge_answer.system.j2")
    for term in ("pass", "partial", "fail", "confidence", "rubric"):
        assert term in system, f"judge prompt missing required term {term!r}"


def test_judge_answer_user_prompt_passes_rubric_through() -> None:
    rendered = render(
        "judge_answer.user.j2",
        item_prompt="What is FastAPI's Depends() for?",
        user_answer="declarative dependencies",
        rubric_pass="names DI",
        rubric_partial="mentions FastAPI only",
        rubric_fail="off-topic",
    )
    for token in (
        "FastAPI's Depends()",
        "declarative dependencies",
        "names DI",
        "mentions FastAPI only",
        "off-topic",
    ):
        assert token in rendered


def test_judge_answer_system_prompt_matches_golden() -> None:
    _check_snapshot("judge_answer.system.j2", _GOLDEN_DIR / "judge_answer.system.txt")


def test_identity_gray_band_system_prompt_contains_decision_space() -> None:
    system = render("identity_gray_band.system.j2")
    for term in ("merge", "new", "pending", "verdict", "confidence", "rationale"):
        assert term in system, f"identity gray-band prompt missing required term {term!r}"


def test_identity_gray_band_user_prompt_passes_pair_through() -> None:
    rendered = render(
        "identity_gray_band.user.j2",
        cosine=0.844,
        candidate_name="fastapi dependency injection",
        candidate_description="FastAPI Depends() pattern",
        existing_name="fastapi depends",
        existing_description="declarative dependencies",
    )
    for token in (
        "0.8440",
        "fastapi dependency injection",
        "FastAPI Depends() pattern",
        "fastapi depends",
        "declarative dependencies",
    ):
        assert token in rendered


def test_identity_gray_band_system_prompt_matches_golden() -> None:
    _check_snapshot(
        "identity_gray_band.system.j2",
        _GOLDEN_DIR / "identity_gray_band.system.txt",
    )


@pytest.mark.parametrize(
    "template",
    [
        "item_gen_replay_self_try.system.j2",
        "item_gen_socratic.system.j2",
        "item_gen_variant.system.j2",
        "item_gen_explain_back.system.j2",
    ],
)
def test_item_gen_system_prompts_match_golden(template: str) -> None:
    """Pin every item_gen system prompt against drift."""
    _check_snapshot(template, _GOLDEN_DIR / template.replace(".j2", ".txt"))


def test_item_gen_replay_self_try_carries_grader_kind() -> None:
    """item_gen prompts must enumerate the grader_kind enum so the parser stays in sync."""
    system = render("item_gen_replay_self_try.system.j2")
    for term in ("grader_kind", "prompt_to_user", "oracle", "hint_path", "estimated_minutes"):
        assert term in system, f"item_gen prompt missing required field {term!r}"


def test_edc_write_definition_system_prompt_matches_golden() -> None:
    """edc_write_definition is on the merge nightly path; pin it."""
    _check_snapshot(
        "edc_write_definition.system.j2",
        _GOLDEN_DIR / "edc_write_definition.system.txt",
    )


def test_edc_verify_merge_system_prompt_matches_golden() -> None:
    """edc_verify_merge gates the auto-merge decision; pin it."""
    _check_snapshot(
        "edc_verify_merge.system.j2",
        _GOLDEN_DIR / "edc_verify_merge.system.txt",
    )


def test_edc_verify_merge_carries_verdict_vocabulary() -> None:
    """The merge judge's verdict enum is parsed by the EDC job; keep it stable."""
    system = render("edc_verify_merge.system.j2")
    for verdict in ("merge", "no_merge", "abstain"):
        assert verdict in system, f"edc_verify_merge prompt missing verdict {verdict!r}"


def test_intervention_socratic_system_prompt_matches_golden() -> None:
    """intervention_socratic is on the real-time pre-AI path; pin it."""
    _check_snapshot(
        "intervention_socratic.system.j2",
        _GOLDEN_DIR / "intervention_socratic.system.txt",
    )


def test_intervention_socratic_carries_output_schema() -> None:
    """The intervention prompt parser depends on {question, hint_path}."""
    system = render("intervention_socratic.system.j2")
    for field_name in ("question", "hint_path", "rationale"):
        assert field_name in system


def _check_snapshot(template_name: str, golden_path: Path) -> None:
    rendered = render(template_name)
    if os.environ.get("SNAPSHOT_UPDATE") == "1" or not golden_path.exists():
        golden_path.write_text(rendered, encoding="utf-8")
        pytest.skip(f"snapshot written to {golden_path}")
    expected = golden_path.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"{template_name} rendering changed.\n"
        f"If intentional, regenerate with SNAPSHOT_UPDATE=1 pytest "
        f"tests/snapshots/test_prompt_snapshots.py."
    )
