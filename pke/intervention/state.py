"""Persistent anti-annoyance state for the intervention decider.

Owns one ``intervention_state`` row per user plus an append-only
``intervention_log`` of every trigger. The decider reads the state to
gate intervention requests and writes the log to record outcomes; this
module is the only place those tables are touched.

Gates resolved here, in priority order:

1. ``deadline_mode_until`` — if in the future, suppress every intervention
   for every source.
2. ``daily_intervention_count`` vs cap — reset to zero on a new day.
3. Per-source ``auto_downgrade_until`` — drops a single source one notch
   for 24 hours after five consecutive dismissals on that source.
4. Per-source ``override_strengths_json`` — user-set override.
5. ``current_strength`` — fallback default for sources without an override.

The single-user-id default is ``"local"`` since PKE is offline-first; a
multi-user iteration can populate the column without schema changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc, new_ulid
from pke.intervention.strength import (
    InterventionThresholds,
    StrengthLevel,
    downgrade,
)

DEFAULT_USER_ID = "local"
_CONSECUTIVE_DISMISS_WINDOW = timedelta(hours=24)


@dataclass(kw_only=True, slots=True)
class InterventionState:
    """Snapshot of the persisted state for one user, decoded for use in code."""

    user_id: str
    current_strength: StrengthLevel
    consecutive_dismiss_count: int
    last_dismiss_at: datetime | None
    last_dismiss_source: str | None
    daily_intervention_count: int
    daily_count_reset_at: str  # ISO date
    deadline_mode_until: datetime | None
    auto_downgrade_until: datetime | None
    override_strengths: dict[str, StrengthLevel] = field(default_factory=dict)


@dataclass(kw_only=True, slots=True)
class InterventionStateStore:
    """Read/write helpers for the two ARCH-4 tables."""

    sqlite: SQLiteStore
    thresholds: InterventionThresholds = field(default_factory=InterventionThresholds)

    def load(self, *, user_id: str = DEFAULT_USER_ID) -> InterventionState:
        """Load the state row for ``user_id``, creating defaults if absent."""
        row = self.sqlite.conn.execute(
            "SELECT * FROM intervention_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return self._create_default(user_id=user_id)
        return _row_to_state(row)

    def save(self, state: InterventionState) -> None:
        """Persist ``state`` back to ``intervention_state``."""
        self.sqlite.conn.execute(
            """
            INSERT INTO intervention_state(
              user_id, current_strength, consecutive_dismiss_count,
              last_dismiss_at, last_dismiss_source,
              daily_intervention_count, daily_count_reset_at,
              deadline_mode_until, auto_downgrade_until, override_strengths_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              current_strength = excluded.current_strength,
              consecutive_dismiss_count = excluded.consecutive_dismiss_count,
              last_dismiss_at = excluded.last_dismiss_at,
              last_dismiss_source = excluded.last_dismiss_source,
              daily_intervention_count = excluded.daily_intervention_count,
              daily_count_reset_at = excluded.daily_count_reset_at,
              deadline_mode_until = excluded.deadline_mode_until,
              auto_downgrade_until = excluded.auto_downgrade_until,
              override_strengths_json = excluded.override_strengths_json
            """,
            (
                state.user_id,
                state.current_strength.value,
                state.consecutive_dismiss_count,
                _iso_or_none(state.last_dismiss_at),
                state.last_dismiss_source,
                state.daily_intervention_count,
                state.daily_count_reset_at,
                _iso_or_none(state.deadline_mode_until),
                _iso_or_none(state.auto_downgrade_until),
                json.dumps({src: lvl.value for src, lvl in state.override_strengths.items()}),
            ),
        )
        self.sqlite.conn.commit()

    def effective_level(
        self, state: InterventionState, *, source: str, now: datetime
    ) -> StrengthLevel:
        """Resolve the effective strength for ``source`` given ``state`` and ``now``."""
        if state.deadline_mode_until is not None and state.deadline_mode_until > now:
            return StrengthLevel.OFF
        override = state.override_strengths.get(source)
        if override is not None:
            return override
        return state.current_strength

    def maybe_reset_daily_count(self, state: InterventionState, *, today: str) -> None:
        """Zero the daily count when the calendar day rolls over (mutates ``state``)."""
        if state.daily_count_reset_at != today:
            state.daily_intervention_count = 0
            state.daily_count_reset_at = today

    def maybe_clear_expired_downgrade(self, state: InterventionState, *, now: datetime) -> None:
        """Drop ``auto_downgrade_until`` and any override it installed once expired."""
        if state.auto_downgrade_until is not None and state.auto_downgrade_until <= now:
            state.auto_downgrade_until = None
            state.override_strengths.clear()

    def record_outcome(
        self,
        state: InterventionState,
        *,
        source: str,
        outcome: str,
        skill_id: str | None = None,
        strength: StrengthLevel,
        socratic_prompt: str | None = None,
        user_response: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Append a row to ``intervention_log`` and update the dismiss counter.

        Returns the new ``log_id``. The caller is responsible for calling
        :meth:`save` after a series of mutations.
        """
        moment = now or datetime.now(tz=UTC)
        log_id = new_ulid()
        self.sqlite.conn.execute(
            """
            INSERT INTO intervention_log(
              log_id, user_id, source, triggered_at, strength_at_trigger,
              skill_id, outcome, socratic_prompt, user_response, user_response_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                state.user_id,
                source,
                _iso(moment),
                strength.value,
                skill_id,
                outcome,
                socratic_prompt,
                user_response,
                _iso(moment) if user_response is not None else None,
            ),
        )
        if outcome == "dismissed":
            self._on_dismiss(state, source=source, now=moment)
        elif outcome == "engaged":
            state.consecutive_dismiss_count = 0
        return log_id

    def increment_daily(self, state: InterventionState) -> None:
        """Increment the daily counter; reset is handled separately."""
        state.daily_intervention_count += 1

    def _on_dismiss(self, state: InterventionState, *, source: str, now: datetime) -> None:
        if (
            state.last_dismiss_at is not None
            and state.last_dismiss_source == source
            and (now - state.last_dismiss_at) <= _CONSECUTIVE_DISMISS_WINDOW
        ):
            state.consecutive_dismiss_count += 1
        else:
            state.consecutive_dismiss_count = 1
        state.last_dismiss_at = now
        state.last_dismiss_source = source

        cap = self.thresholds.consecutive_dismiss_downgrade
        if state.consecutive_dismiss_count >= cap:
            current = state.override_strengths.get(source, state.current_strength)
            state.override_strengths[source] = downgrade(current)
            state.auto_downgrade_until = now + timedelta(hours=24)
            state.consecutive_dismiss_count = 0

    def _create_default(self, *, user_id: str) -> InterventionState:
        today = datetime.now(tz=UTC).date().isoformat()
        state = InterventionState(
            user_id=user_id,
            current_strength=StrengthLevel.GENTLE,
            consecutive_dismiss_count=0,
            last_dismiss_at=None,
            last_dismiss_source=None,
            daily_intervention_count=0,
            daily_count_reset_at=today,
            deadline_mode_until=None,
            auto_downgrade_until=None,
            override_strengths={},
        )
        self.save(state)
        return state

    def set_deadline_mode(
        self, *, hours: float, user_id: str = DEFAULT_USER_ID
    ) -> InterventionState:
        """Engage deadline mode for ``hours``; everything is OFF until it expires."""
        state = self.load(user_id=user_id)
        state.deadline_mode_until = datetime.now(tz=UTC) + timedelta(hours=hours)
        self.save(state)
        return state

    def set_override(
        self,
        *,
        source: str,
        level: StrengthLevel,
        user_id: str = DEFAULT_USER_ID,
    ) -> InterventionState:
        """User-driven override for a single source."""
        state = self.load(user_id=user_id)
        state.override_strengths[source] = level
        self.save(state)
        return state


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _iso_or_none(value: datetime | None) -> str | None:
    return _iso(value) if value is not None else None


def _int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(str(value))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(raw).astimezone(UTC)


def _row_to_state(row: dict[str, object]) -> InterventionState:
    overrides_raw = row["override_strengths_json"]
    overrides: dict[str, StrengthLevel] = {}
    if overrides_raw:
        for key, value in json.loads(str(overrides_raw)).items():
            try:
                overrides[str(key)] = StrengthLevel(str(value))
            except ValueError:
                continue
    return InterventionState(
        user_id=str(row["user_id"]),
        current_strength=StrengthLevel(str(row["current_strength"])),
        consecutive_dismiss_count=_int(row["consecutive_dismiss_count"]),
        last_dismiss_at=_parse_iso(str(row["last_dismiss_at"]) if row["last_dismiss_at"] else None),
        last_dismiss_source=str(row["last_dismiss_source"]) if row["last_dismiss_source"] else None,
        daily_intervention_count=_int(row["daily_intervention_count"]),
        daily_count_reset_at=str(row["daily_count_reset_at"]),
        deadline_mode_until=_parse_iso(
            str(row["deadline_mode_until"]) if row["deadline_mode_until"] else None
        ),
        auto_downgrade_until=_parse_iso(
            str(row["auto_downgrade_until"]) if row["auto_downgrade_until"] else None
        ),
        override_strengths=overrides,
    )


__all__ = [
    "DEFAULT_USER_ID",
    "InterventionState",
    "InterventionStateStore",
    "iso_utc",
]
