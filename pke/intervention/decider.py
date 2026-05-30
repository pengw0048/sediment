"""Intervention trigger and anti-annoyance logic.

Two deciders live here:

* :class:`InterventionDecider` keeps its state in-memory. Used by the
  legacy in-process tests and by call sites that explicitly want a
  short-lived decider (e.g. ephemeral browser tabs).
* :class:`PersistentInterventionDecider` reads and writes the
  ``intervention_state`` / ``intervention_log`` tables (ARCH-4) so the
  daily cap, consecutive-dismiss downgrade, and deadline mode survive
  across process restarts. Production paths should prefer this one.

Both expose the same ``should_intervene`` shape so callers can swap
without changing their request handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pke.db.sqlite import SQLiteStore
from pke.intervention.state import (
    DEFAULT_USER_ID,
    InterventionState,
    InterventionStateStore,
)
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
    """Decide whether to intervene for a resolved skill (in-memory variant)."""

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


@dataclass(kw_only=True, slots=True)
class PersistentInterventionDecider:
    """ARCH-4 decider that reads and writes ``intervention_state``.

    Each ``should_intervene`` call:

    1. Loads the user's state row (creating defaults on first use).
    2. Rolls the daily counter over if the calendar day changed.
    3. Clears an expired auto-downgrade window if one is in effect.
    4. Runs the gate chain (deadline → daily cap → mastery band →
       gentle-N cooldown).
    5. On a positive decision, increments the daily counter, persists
       the new state, and writes a ``shown`` row to
       ``intervention_log``.

    Outcome recording (``dismissed`` / ``engaged`` / ``bypassed``) flows
    through :meth:`record_outcome`, which mutates the dismiss counter
    and may install a 24-hour auto-downgrade for the offending source.
    """

    sqlite: SQLiteStore
    thresholds: InterventionThresholds = field(default_factory=InterventionThresholds)
    user_id: str = DEFAULT_USER_ID
    counters: dict[str, int] = field(default_factory=dict)
    _store: InterventionStateStore = field(init=False)

    def __post_init__(self) -> None:
        self._store = InterventionStateStore(sqlite=self.sqlite, thresholds=self.thresholds)

    def should_intervene(
        self,
        *,
        source: str,
        skill_id: str,
        skill_label: str,
        unaided_mastery: float,
        task_type: str = "learn",
        now: datetime | None = None,
    ) -> InterventionPayload | None:
        """Return a payload or None after applying ARCH-4 gates."""
        moment = now or datetime.now(tz=UTC)
        state = self._store.load(user_id=self.user_id)
        self._store.maybe_reset_daily_count(state, today=moment.date().isoformat())
        self._store.maybe_clear_expired_downgrade(state, now=moment)

        if not (self.thresholds.mastery_lower < unaided_mastery < self.thresholds.mastery_upper):
            self._store.save(state)
            return None
        if task_type in self.thresholds.exempt_task_types:
            self._store.save(state)
            return None
        if state.daily_intervention_count >= self.thresholds.daily_intervention_cap:
            self._store.save(state)
            return None

        level = self._store.effective_level(state, source=source, now=moment)
        if level is StrengthLevel.OFF:
            self._store.save(state)
            return None

        key = f"{source}:{skill_id}"
        if level is StrengthLevel.GENTLE:
            self.counters[key] = self.counters.get(key, 0) + 1
            if self.counters[key] % self.thresholds.gentle_every_n != 0:
                self._store.save(state)
                return None

        mode = "post_response_toast" if level is StrengthLevel.QUIET else "pre_ai"
        question = f"Before AI answers, what would you check first for {skill_label}?"
        hint_path = [
            "Name the first observable signal.",
            "Recall a similar task you have already seen.",
            "Sketch the answer shape, then ask AI for details if needed.",
        ]
        self._store.increment_daily(state)
        self._store.record_outcome(
            state,
            source=source,
            outcome="shown",
            skill_id=skill_id,
            strength=level,
            socratic_prompt=question,
            now=moment,
        )
        self._store.save(state)
        return InterventionPayload(
            mode=mode,
            strength=level,
            skill_id=skill_id,
            question=question,
            hint_path=hint_path,
        )

    def record_outcome(
        self,
        *,
        source: str,
        outcome: str,
        skill_id: str | None = None,
        user_response: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Persist an outcome on the latest shown intervention for ``source``.

        Returns the new ``log_id``. The dismiss counter is mutated as a
        side effect; consult :class:`InterventionStateStore` for the
        rules.
        """
        moment = now or datetime.now(tz=UTC)
        state = self._store.load(user_id=self.user_id)
        level = self._store.effective_level(state, source=source, now=moment)
        log_id = self._store.record_outcome(
            state,
            source=source,
            outcome=outcome,
            skill_id=skill_id,
            strength=level,
            user_response=user_response,
            now=moment,
        )
        self._store.save(state)
        return log_id

    def load_state(self) -> InterventionState:
        """Return the current persisted state (caller-visible snapshot)."""
        return self._store.load(user_id=self.user_id)


__all__ = [
    "InterventionDecider",
    "InterventionPayload",
    "PersistentInterventionDecider",
]
