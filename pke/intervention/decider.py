"""Intervention trigger and anti-annoyance logic."""

from __future__ import annotations

from dataclasses import dataclass, field

from pke.intervention.strength import InterventionThresholds, StrengthLevel, downgrade


@dataclass(frozen=True, kw_only=True, slots=True)
class InterventionPayload:
    """Renderable intervention payload."""

    mode: str
    strength: StrengthLevel
    skill_id: str
    question: str
    hint_path: list[str]
    bypass_label: str = "Skip, just ask AI"


@dataclass(kw_only=True, slots=True)
class InterventionDecider:
    """Decide whether to intervene for a resolved skill."""

    thresholds: InterventionThresholds = field(default_factory=InterventionThresholds)
    per_source: dict[str, StrengthLevel] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    outcomes: list[str] = field(default_factory=list)
    deadline_mode: bool = False

    def level_for_source(self, source: str) -> StrengthLevel:
        """Return effective level for a source."""
        if self.deadline_mode:
            return StrengthLevel.OFF
        return self.per_source.get(source, StrengthLevel.QUIET)

    def record_outcome(self, outcome: str) -> None:
        """Record an outcome and auto-downgrade on repeated dismissals."""
        self.outcomes.append(outcome)
        self.outcomes = self.outcomes[-20:]
        if (
            self.outcomes[-self.thresholds.consecutive_dismiss_downgrade :]
            == ["dismissed_immediately"] * self.thresholds.consecutive_dismiss_downgrade
        ):
            for source, level in list(self.per_source.items()):
                self.per_source[source] = downgrade(level)

    def should_intervene(
        self,
        *,
        source: str,
        skill_id: str,
        skill_label: str,
        unaided_mastery: float,
        task_type: str = "learn",
    ) -> InterventionPayload | None:
        """Return a payload or None after applying gates."""
        level = self.level_for_source(source)
        if level is StrengthLevel.OFF:
            return None
        if task_type in self.thresholds.exempt_task_types:
            return None
        if not (self.thresholds.mastery_lower < unaided_mastery < self.thresholds.mastery_upper):
            return None
        key = f"{source}:{skill_id}"
        if level is StrengthLevel.GENTLE:
            self.counters[key] = self.counters.get(key, 0) + 1
            if self.counters[key] % self.thresholds.gentle_every_n != 0:
                return None
        mode = "post_response_toast" if level is StrengthLevel.QUIET else "pre_ai"
        return InterventionPayload(
            mode=mode,
            strength=level,
            skill_id=skill_id,
            question=f"Before AI answers, what would you check first for {skill_label}?",
            hint_path=[
                "Name the first observable signal.",
                "Recall a similar task you have already seen.",
                "Sketch the answer shape, then ask AI for details if needed.",
            ],
        )
