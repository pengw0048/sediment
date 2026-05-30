"""ARCH-4 anti-annoyance state and gate tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pke.intervention.decider import PersistentInterventionDecider
from pke.intervention.state import (
    DEFAULT_USER_ID,
    InterventionStateStore,
)
from pke.intervention.strength import InterventionThresholds, StrengthLevel


def _decider(app, **overrides) -> PersistentInterventionDecider:
    thresholds = InterventionThresholds(
        mastery_lower=0.10,
        mastery_upper=0.95,
        gentle_every_n=1,
        daily_intervention_cap=overrides.get("daily_intervention_cap", 100),
        consecutive_dismiss_downgrade=overrides.get("consecutive_dismiss_downgrade", 5),
    )
    return PersistentInterventionDecider(sqlite=app.sqlite, thresholds=thresholds)


def test_first_call_creates_state_with_gentle_default(app) -> None:
    """A first should_intervene call lazily creates an intervention_state row."""
    decider = _decider(app)

    payload = decider.should_intervene(
        source="claude_code",
        skill_id="skill_1",
        skill_label="kubectl describe",
        unaided_mastery=0.5,
    )

    assert payload is not None
    state = decider.load_state()
    assert state.current_strength is StrengthLevel.GENTLE
    assert state.daily_intervention_count == 1


def test_deadline_mode_blocks_every_source(app) -> None:
    """When deadline_mode_until is in the future, every source returns None."""
    store = InterventionStateStore(sqlite=app.sqlite)
    store.set_deadline_mode(hours=2.0)
    decider = _decider(app)

    payload = decider.should_intervene(
        source="claude_code",
        skill_id="skill_1",
        skill_label="kubectl",
        unaided_mastery=0.5,
    )

    assert payload is None
    state = decider.load_state()
    assert state.daily_intervention_count == 0


def test_daily_cap_blocks_after_threshold(app) -> None:
    """Hitting daily_intervention_cap suppresses further interventions until rollover."""
    decider = _decider(app, daily_intervention_cap=2)

    first = decider.should_intervene(
        source="claude_code", skill_id="a", skill_label="a", unaided_mastery=0.4
    )
    second = decider.should_intervene(
        source="claude_code", skill_id="b", skill_label="b", unaided_mastery=0.5
    )
    third = decider.should_intervene(
        source="claude_code", skill_id="c", skill_label="c", unaided_mastery=0.6
    )

    assert first is not None
    assert second is not None
    assert third is None


def test_five_consecutive_dismissals_drop_source_one_notch(app) -> None:
    """Five dismissals in a row on one source install a 24h auto-downgrade for it."""
    decider = _decider(app, consecutive_dismiss_downgrade=5)

    # Set the user override so we have an explicit per-source level we can watch.
    store = InterventionStateStore(sqlite=app.sqlite)
    state = store.load()
    state.override_strengths["claude_code"] = StrengthLevel.GENTLE
    store.save(state)

    now = datetime.now(tz=UTC)
    for _ in range(5):
        decider.record_outcome(source="claude_code", outcome="dismissed", now=now)

    state = decider.load_state()
    assert state.override_strengths["claude_code"] is StrengthLevel.QUIET
    assert state.auto_downgrade_until is not None
    assert state.auto_downgrade_until > now


def test_engage_resets_consecutive_counter(app) -> None:
    """A single ``engaged`` outcome clears the streak before it can trigger downgrade."""
    decider = _decider(app, consecutive_dismiss_downgrade=5)

    now = datetime.now(tz=UTC)
    for _ in range(4):
        decider.record_outcome(source="claude_code", outcome="dismissed", now=now)
    decider.record_outcome(source="claude_code", outcome="engaged", now=now)

    state = decider.load_state()
    assert state.consecutive_dismiss_count == 0
    assert "claude_code" not in state.override_strengths


def test_auto_downgrade_expires_after_24h(app) -> None:
    """When auto_downgrade_until is in the past, the override clears on next call."""
    store = InterventionStateStore(sqlite=app.sqlite)
    state = store.load()
    state.override_strengths["claude_code"] = StrengthLevel.OFF
    state.auto_downgrade_until = datetime.now(tz=UTC) - timedelta(hours=1)
    store.save(state)

    decider = _decider(app)
    payload = decider.should_intervene(
        source="claude_code",
        skill_id="x",
        skill_label="x",
        unaided_mastery=0.5,
    )

    assert payload is not None  # override cleared, default gentle applies
    state = decider.load_state()
    assert state.auto_downgrade_until is None
    assert state.override_strengths == {}


def test_daily_counter_rolls_over_on_new_day(app) -> None:
    """When the date column lags reality, the daily counter resets before the cap check."""
    store = InterventionStateStore(sqlite=app.sqlite)
    state = store.load()
    state.daily_intervention_count = 99
    state.daily_count_reset_at = "1999-01-01"
    store.save(state)

    decider = _decider(app, daily_intervention_cap=5)
    payload = decider.should_intervene(
        source="claude_code", skill_id="x", skill_label="x", unaided_mastery=0.5
    )
    assert payload is not None
    state = decider.load_state()
    assert state.daily_intervention_count == 1


def test_intervention_log_grows_on_each_outcome(app) -> None:
    """Every record_outcome call appends one row to intervention_log."""
    decider = _decider(app)
    decider.should_intervene(
        source="claude_code", skill_id="x", skill_label="x", unaided_mastery=0.4
    )
    decider.record_outcome(source="claude_code", outcome="dismissed")
    decider.record_outcome(source="claude_code", outcome="engaged")

    count = app.sqlite.conn.execute(
        "SELECT COUNT(*) AS c FROM intervention_log WHERE user_id = ?",
        (DEFAULT_USER_ID,),
    ).fetchone()["c"]
    assert count == 3


def test_mastery_out_of_band_does_not_increment_daily(app) -> None:
    """Mastery outside [lower, upper] skips intervention without burning a daily slot."""
    decider = _decider(app)
    payload = decider.should_intervene(
        source="claude_code", skill_id="x", skill_label="x", unaided_mastery=0.99
    )
    assert payload is None
    state = decider.load_state()
    assert state.daily_intervention_count == 0


@pytest.mark.parametrize(
    "task_type",
    ["debug", "ship"],
)
def test_exempt_task_types_bypass_intervention(app, task_type: str) -> None:
    """Exempt task types skip intervention and do not eat into the daily cap."""
    decider = _decider(app)
    payload = decider.should_intervene(
        source="claude_code",
        skill_id="x",
        skill_label="x",
        unaided_mastery=0.5,
        task_type=task_type,
    )
    assert payload is None
    state = decider.load_state()
    assert state.daily_intervention_count == 0
