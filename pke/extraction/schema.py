"""Structured output schema for skill extraction and judging."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


def clamp01(value: float) -> float:
    """Clamp ``value`` into ``[0.0, 1.0]`` for LLM-supplied confidences.

    LLM call sites occasionally return out-of-band values (1.5 from a
    miscalibrated model, -0.01 from a parser rounding error). Pass every
    LLM-supplied confidence through here before persisting it so the
    downstream cap and decision thresholds see a well-formed input.
    """
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


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
    span: ExtractedSpan = field(default_factory=ExtractedSpan)

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
