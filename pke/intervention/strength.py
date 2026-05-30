"""Intervention strength levels and thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StrengthLevel(StrEnum):
    """Four frozen intervention levels."""

    OFF = "off"
    QUIET = "quiet"
    GENTLE = "gentle"
    ACTIVE = "active"


@dataclass(frozen=True, kw_only=True, slots=True)
class InterventionThresholds:
    """Thresholds used by intervention gating."""

    mastery_lower: float = 0.20
    mastery_upper: float = 0.70
    skill_dedup_cosine: float = 0.18
    outsourcing_half_life_days: float = 14.0
    skill_cooldown_minutes: int = 60
    gentle_every_n: int = 30
    daily_intervention_cap: int = 12
    consecutive_dismiss_downgrade: int = 5
    deadline_mode_minutes: int = 120
    exempt_task_types: tuple[str, ...] = ("debug", "ship")


def downgrade(level: StrengthLevel) -> StrengthLevel:
    """Downgrade one intervention level."""
    return {
        StrengthLevel.ACTIVE: StrengthLevel.GENTLE,
        StrengthLevel.GENTLE: StrengthLevel.QUIET,
        StrengthLevel.QUIET: StrengthLevel.OFF,
        StrengthLevel.OFF: StrengthLevel.OFF,
    }[level]
