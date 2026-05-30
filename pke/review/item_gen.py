"""Review item generation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum

from pke.extraction.llm_client import LLMClient
from pke.extraction.prompts import render as render_prompt


class ReviewItemType(StrEnum):
    """Five v1 review item types."""

    REPLAY_SELF_TRY = "replay_self_try"
    SOCRATIC = "socratic"
    VARIANT = "variant"
    EXPLAIN_BACK = "explain_back"
    CALIBRATION_ONLY = "calibration_only"


# Mapping from item type to the system-prompt template name. ``CALIBRATION_ONLY``
# has no LLM prompt: it is a fixed self-report template.
_SYSTEM_PROMPTS: dict[ReviewItemType, str] = {
    ReviewItemType.REPLAY_SELF_TRY: "item_gen_replay_self_try.system.j2",
    ReviewItemType.SOCRATIC: "item_gen_socratic.system.j2",
    ReviewItemType.VARIANT: "item_gen_variant.system.j2",
    ReviewItemType.EXPLAIN_BACK: "item_gen_explain_back.system.j2",
}

_VALID_GRADERS: frozenset[str] = frozenset(
    {"llm_judge", "regex", "code_exec", "self_report", "manual"}
)


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


def _mastery_band(unaided: float) -> str:
    """Coarse label used in the user prompt to give the LLM a target difficulty."""
    if unaided < 0.2:
        return "encountered"
    if unaided < 0.5:
        return "practicing"
    if unaided < 0.8:
        return "familiar"
    return "fluent"


@dataclass(kw_only=True, slots=True)
class ItemGenerator:
    """Generate review items via LLM, with a deterministic fallback.

    Pass an ``LLMClient`` to get LLM-authored items (the four non-calibration
    types). Without a client the generator falls back to short hard-coded
    templates so review sessions still produce something; the templates are
    explicit fallbacks, not the supported quality target.
    """

    client: LLMClient | None = field(default=None)
    fallback_hint_path: tuple[str, str, str] = (
        "Name the smallest subproblem first.",
        "Recall what signal told you the original answer was needed.",
        "Describe the shape of the answer before details.",
    )

    async def generate(
        self,
        *,
        skill_label: str,
        evidence_text: str,
        unaided_mastery: float,
        evidence_count: int = 1,
        item_type: ReviewItemType | None = None,
        skill_description: str = "",
        recent_outsource_count: int | None = None,
    ) -> GeneratedItem:
        """Generate one review item."""
        chosen = item_type or pick_item_type(unaided=unaided_mastery, evidence_count=evidence_count)
        if chosen is ReviewItemType.CALIBRATION_ONLY:
            return self._calibration_item(skill_label)
        if self.client is None:
            return self._fallback(chosen, skill_label, evidence_text)
        return await self._llm_item(
            chosen,
            skill_label=skill_label,
            skill_description=skill_description,
            evidence_text=evidence_text,
            unaided_mastery=unaided_mastery,
            recent_outsource_count=recent_outsource_count,
        )

    async def _llm_item(
        self,
        chosen: ReviewItemType,
        *,
        skill_label: str,
        skill_description: str,
        evidence_text: str,
        unaided_mastery: float,
        recent_outsource_count: int | None,
    ) -> GeneratedItem:
        assert self.client is not None  # narrowed by caller
        system = render_prompt(_SYSTEM_PROMPTS[chosen])
        user = render_prompt(
            "item_gen.user.j2",
            skill_label=skill_label,
            skill_description=skill_description,
            evidence_text=evidence_text,
            mastery_band=_mastery_band(unaided_mastery),
            recent_outsource_count=recent_outsource_count,
        )
        payload = await self.client.complete_json(system=system, user=user)
        return self._parse_item(chosen, payload)

    def _parse_item(self, item_type: ReviewItemType, payload: dict[str, object]) -> GeneratedItem:
        prompt = str(payload.get("prompt_to_user", "")).strip()
        if not prompt:
            raise ValueError(f"item_gen LLM returned empty prompt_to_user: {payload!r}")
        grader = str(payload.get("grader_kind", "llm_judge"))
        if grader not in _VALID_GRADERS:
            grader = "llm_judge"
        oracle_raw = payload.get("oracle")
        oracle = self._serialize_oracle(oracle_raw)
        hint_path_raw = payload.get("hint_path") or []
        hint_path = (
            [str(h) for h in hint_path_raw]
            if isinstance(hint_path_raw, list)
            else list(self.fallback_hint_path)
        )
        return GeneratedItem(
            item_type=item_type,
            prompt=prompt,
            oracle=oracle,
            grader=grader,
            hint_path=hint_path,
        )

    @staticmethod
    def _serialize_oracle(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    def _calibration_item(self, skill_label: str) -> GeneratedItem:
        return GeneratedItem(
            item_type=ReviewItemType.CALIBRATION_ONLY,
            prompt=f"How confident are you that you can do {skill_label} without help?",
            oracle=None,
            grader="self_report",
            hint_path=[],
        )

    def _fallback(
        self, chosen: ReviewItemType, skill_label: str, evidence_text: str
    ) -> GeneratedItem:
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
            hint_path=list(self.fallback_hint_path),
        )
