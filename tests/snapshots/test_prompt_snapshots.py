"""Snapshot-style prompt checks."""

from pathlib import Path


def test_extraction_prompt_contains_frozen_schema_terms():
    prompt = Path("pke/extraction/prompts/extract_skills.j2").read_text(encoding="utf-8")
    assert "demonstrated" in prompt
    assert "attempted" in prompt
    assert "failed" in prompt
    assert "asked-about" in prompt
