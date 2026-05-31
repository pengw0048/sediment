"""Five-level mastery band rendering for the /skills overview.

Bands are a coarse summary of where a skill sits on the mastery
ladder, computed from ``unaided_retrievability`` and ``unaided_reps``
in the mastery state. They exist because the underlying floats are
not human-scanable — five filled circles tell a researcher at a
glance whether a skill is fluent or still being practiced.

The thresholds below are a deliberate compromise:

* ``UNSEEN``       — no unaided reps yet; nothing to grade.
* ``ENCOUNTERED``  — at least one rep, but either too few reps or
  too-low retrievability to call the skill "practiced".
* ``PRACTICING``   — retrievability >= 0.4 (the skill is starting
  to stick).
* ``COMPETENT``    — retrievability >= 0.7 and reps >= 3.
* ``FLUENT``       — retrievability >= 0.85 and reps >= 5.

The thresholds are set so a skill marches steadily up the ladder as
the unaided-recall score rises and more attempts are recorded; they
do not pretend to track FSRS internals.
"""

from __future__ import annotations

from enum import Enum
from typing import Final


class MasteryBand(Enum):
    """Coarse human-readable mastery level rendered next to a skill."""

    UNSEEN = ("UNSEEN", "○○○○○")
    ENCOUNTERED = ("ENCOUNTERED", "●○○○○")
    PRACTICING = ("PRACTICING", "●●○○○")
    COMPETENT = ("COMPETENT", "●●●●○")
    FLUENT = ("FLUENT", "●●●●●")

    def __init__(self, label: str, glyph: str) -> None:
        self._label = label
        self._glyph = glyph

    @property
    def label(self) -> str:
        """Return the short human label, e.g. ``"PRACTICING"``."""
        return self._label

    @property
    def glyph(self) -> str:
        """Return the five-character bullet glyph for this band."""
        return self._glyph


# Boundary constants are pulled out so test cases reference the same
# values as the implementation and a single change updates both.
FLUENT_MIN_UNAIDED: Final = 0.85
FLUENT_MIN_REPS: Final = 5
COMPETENT_MIN_UNAIDED: Final = 0.7
COMPETENT_MIN_REPS: Final = 3
PRACTICING_MIN_UNAIDED: Final = 0.4


def band_from_mastery(*, unaided: float, reps: int) -> MasteryBand:
    """Bucket a skill's unaided retrievability and rep count into a band.

    The order of the checks matters: we descend from the strictest
    band first so a single skill with one high score but only one rep
    still lands at ``ENCOUNTERED`` rather than ``FLUENT``.
    """
    if reps <= 0:
        return MasteryBand.UNSEEN
    if unaided >= FLUENT_MIN_UNAIDED and reps >= FLUENT_MIN_REPS:
        return MasteryBand.FLUENT
    if unaided >= COMPETENT_MIN_UNAIDED and reps >= COMPETENT_MIN_REPS:
        return MasteryBand.COMPETENT
    if unaided >= PRACTICING_MIN_UNAIDED:
        return MasteryBand.PRACTICING
    return MasteryBand.ENCOUNTERED


__all__ = [
    "COMPETENT_MIN_REPS",
    "COMPETENT_MIN_UNAIDED",
    "FLUENT_MIN_REPS",
    "FLUENT_MIN_UNAIDED",
    "MasteryBand",
    "PRACTICING_MIN_UNAIDED",
    "band_from_mastery",
]
