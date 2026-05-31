"""MasteryBand boundary coverage."""

from __future__ import annotations

import pytest

from pke.mastery.bands import MasteryBand, band_from_mastery


@pytest.mark.parametrize(
    ("unaided", "reps", "expected"),
    [
        # ------- UNSEEN ---------
        (0.0, 0, MasteryBand.UNSEEN),
        (0.95, 0, MasteryBand.UNSEEN),  # reps short-circuits to UNSEEN
        # ------- ENCOUNTERED ---------
        (0.0, 1, MasteryBand.ENCOUNTERED),
        (0.39, 1, MasteryBand.ENCOUNTERED),  # under the practicing floor
        (0.39, 99, MasteryBand.ENCOUNTERED),  # plenty of reps, score too low
        # ------- PRACTICING ---------
        (0.4, 1, MasteryBand.PRACTICING),  # at the practicing floor
        (0.69, 2, MasteryBand.PRACTICING),  # not enough reps for COMPETENT
        (0.85, 2, MasteryBand.PRACTICING),  # fluent score, too few reps for COMPETENT (3)
        # ------- COMPETENT ---------
        (0.7, 3, MasteryBand.COMPETENT),  # at both floors
        (0.84, 4, MasteryBand.COMPETENT),  # almost fluent
        (0.95, 4, MasteryBand.COMPETENT),  # high score but reps < 5
        # ------- FLUENT ---------
        (0.85, 5, MasteryBand.FLUENT),  # at the fluent floor on both axes
        (1.0, 20, MasteryBand.FLUENT),
    ],
)
def test_band_from_mastery_buckets_each_boundary(
    unaided: float, reps: int, expected: MasteryBand
) -> None:
    assert band_from_mastery(unaided=unaided, reps=reps) is expected


def test_glyphs_are_five_characters_each() -> None:
    """All bands render exactly five glyph characters so layout stays aligned."""
    for band in MasteryBand:
        assert len(band.glyph) == 5, f"{band.name} glyph must be 5 chars"


def test_unseen_short_circuits_even_when_reps_negative() -> None:
    """Defensive: a corrupt negative reps count still resolves to UNSEEN."""
    assert band_from_mastery(unaided=0.99, reps=-1) is MasteryBand.UNSEEN
