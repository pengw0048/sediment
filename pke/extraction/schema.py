"""Structured output schema for skill extraction and judging."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Polarity(StrEnum):
    """Extraction polarity from the frozen v1 schema."""

    DEMONSTRATED = "demonstrated"
    ATTEMPTED = "attempted"
    FAILED = "failed"
    ASKED_ABOUT = "asked-about"


POLARITY_TO_EVIDENCE_KIND = {
    Polarity.DEMONSTRATED: "demonstrated",
    Polarity.ATTEMPTED: "executed",
    Polarity.FAILED: "failed",
    Polarity.ASKED_ABOUT: "asked",
}


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractedSpan:
    """Character span where a skill was observed."""

    start: int | None = None
    end: int | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractedSkill:
    """One skill extracted from one evidence item."""

    raw_name: str
    normalized_name: str
    description: str
    polarity: Polarity
    confidence: float
    span: ExtractedSpan = ExtractedSpan()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@dataclass(frozen=True, kw_only=True, slots=True)
class JudgeVerdict:
    """LLM judge output for review grading."""

    grade: str
    confidence: float
    feedback: str
    demonstrates_skill: bool
