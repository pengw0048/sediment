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
    rendered = render("extract_skills.system.j2")
    golden_path = _GOLDEN_DIR / "extract_skills.system.txt"
    if os.environ.get("SNAPSHOT_UPDATE") == "1" or not golden_path.exists():
        golden_path.write_text(rendered, encoding="utf-8")
        pytest.skip(f"snapshot written to {golden_path}")
    expected = golden_path.read_text(encoding="utf-8")
    assert rendered == expected, (
        "extract_skills.system.j2 rendering changed.\n"
        "If intentional, regenerate with SNAPSHOT_UPDATE=1 pytest "
        "tests/snapshots/test_prompt_snapshots.py."
    )
