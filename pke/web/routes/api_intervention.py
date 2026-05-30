"""Intervention API routes (ARCH-4 persistent backing)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pke.intervention.decider import PersistentInterventionDecider
from pke.intervention.state import InterventionStateStore
from pke.intervention.strength import StrengthLevel
from pke.intervention.toast import toast_payload

_VALID_OUTCOMES = {"shown", "dismissed", "engaged", "bypassed"}


def router(store_getter: Any) -> APIRouter:
    """Build intervention API routes."""
    api = APIRouter(prefix="/api/v1/intervention")

    @api.post("/check")
    async def check(payload: dict[str, Any]) -> dict[str, Any]:
        app = store_getter()
        decider = PersistentInterventionDecider(sqlite=app.sqlite)
        intervention = decider.should_intervene(
            source=str(payload.get("source", "browser_ext")),
            skill_id=str(payload.get("skill_id", "unknown")),
            skill_label=str(payload.get("skill_label", "this skill")),
            unaided_mastery=float(payload.get("unaided_mastery", 0.5)),
            task_type=str(payload.get("task_type", "learn")),
        )
        if intervention is None:
            return {"intervene": False}
        return {"intervene": True, "payload": toast_payload(intervention)}

    @api.post("/outcome")
    async def outcome(payload: dict[str, Any]) -> dict[str, Any]:
        app = store_getter()
        outcome_kind = str(payload.get("outcome", ""))
        if outcome_kind not in _VALID_OUTCOMES:
            raise HTTPException(status_code=422, detail=f"unknown outcome: {outcome_kind!r}")
        decider = PersistentInterventionDecider(sqlite=app.sqlite)
        log_id = decider.record_outcome(
            source=str(payload.get("source", "browser_ext")),
            outcome=outcome_kind,
            skill_id=payload.get("skill_id"),
            user_response=payload.get("user_response"),
        )
        return {"log_id": log_id}

    @api.post("/deadline_mode")
    async def deadline_mode(payload: dict[str, Any]) -> dict[str, Any]:
        app = store_getter()
        hours = float(payload.get("hours", 2.0))
        if hours <= 0:
            raise HTTPException(status_code=422, detail="hours must be positive")
        store = InterventionStateStore(sqlite=app.sqlite)
        state = store.set_deadline_mode(hours=hours)
        return {
            "deadline_mode_until": state.deadline_mode_until.isoformat()
            if state.deadline_mode_until
            else None,
        }

    @api.post("/override")
    async def override(payload: dict[str, Any]) -> dict[str, Any]:
        app = store_getter()
        level_raw = str(payload.get("level", "")).lower()
        try:
            level = StrengthLevel(level_raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid level: {level_raw!r}") from exc
        source = str(payload.get("source", ""))
        if not source:
            raise HTTPException(status_code=422, detail="source is required")
        store = InterventionStateStore(sqlite=app.sqlite)
        store.set_override(source=source, level=level)
        return {"source": source, "level": level.value}

    return api
