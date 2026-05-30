"""Review item generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReviewItemType(StrEnum):
    """Five v1 review item types."""

    REPLAY_SELF_TRY = "replay_self_try"
    SOCRATIC = "socratic"
    VARIANT = "variant"
    EXPLAIN_BACK = "explain_back"
    CALIBRATION_ONLY = "calibration_only"


@dataclass(frozen=True, kw_only=True, slots=True)
class GeneratedItem:
    """Generated review item payload."""

    item_type: ReviewItemType
    prompt: str
    oracle: str | None
    grader: str
    hint_path: list[str]


def pick_item_type(*, unaided: float, evidence_count: int) -> ReviewItemType:
    """Pick an item type based on mastery and evidence density."""
    if evidence_count < 2:
        return ReviewItemType.CALIBRATION_ONLY
    if unaided < 0.3:
        return ReviewItemType.SOCRATIC
    if unaided < 0.6:
        return ReviewItemType.REPLAY_SELF_TRY
    return ReviewItemType.VARIANT


@dataclass(kw_only=True, slots=True)
class ItemGenerator:
    """Generate review items without requiring an online LLM."""

    def generate(
        self,
        *,
        skill_label: str,
        evidence_text: str,
        unaided_mastery: float,
        evidence_count: int = 1,
        item_type: ReviewItemType | None = None,
    ) -> GeneratedItem:
        """Generate one item or fallback to calibration-only."""
        chosen = item_type or pick_item_type(unaided=unaided_mastery, evidence_count=evidence_count)
        if chosen is ReviewItemType.CALIBRATION_ONLY:
            return GeneratedItem(
                item_type=chosen,
                prompt=f"How confident are you that you can do {skill_label} without help?",
                oracle=None,
                grader="self_report",
                hint_path=[],
            )
        if chosen is ReviewItemType.SOCRATIC:
            prompt = f"For {skill_label}, what is the first concrete thing you would check?"
            grader = "llm_judge"
        elif chosen is ReviewItemType.VARIANT:
            prompt = (
                f"Try a nearby variant of {skill_label}: change one input and explain the result."
            )
            grader = "llm_judge"
        elif chosen is ReviewItemType.EXPLAIN_BACK:
            prompt = f"Explain {skill_label} in your own words to a non-expert."
            grader = "llm_judge"
        else:
            prompt = f"You asked about this before. Try it yourself:\n\n{evidence_text[:1000]}"
            grader = "manual"
        return GeneratedItem(
            item_type=chosen,
            prompt=prompt,
            oracle=None,
            grader=grader,
            hint_path=[
                "Name the smallest subproblem first.",
                "Recall what signal told you the original answer was needed.",
                "Describe the shape of the answer before details.",
            ],
        )
