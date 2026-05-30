"""Intervention API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pke.intervention.decider import InterventionDecider
from pke.intervention.strength import StrengthLevel
from pke.intervention.toast import toast_payload


def router(store_getter: Any) -> APIRouter:
    """Build intervention API routes."""
    del store_getter
    api = APIRouter(prefix="/api/v1/intervention")
    decider = InterventionDecider(per_source={"browser_ext": StrengthLevel.GENTLE})

    @api.post("/check")
    async def check(payload: dict[str, Any]) -> dict[str, Any]:
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

    return api
