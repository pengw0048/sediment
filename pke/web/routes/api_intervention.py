"""Intervention API routes (ARCH-4 persistent backing)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pke.identity.embedder import Embedder
from pke.intervention.decider import PersistentInterventionDecider
from pke.intervention.skill_resolver import resolve_prompt_to_skill
from pke.intervention.state import InterventionStateStore
from pke.intervention.strength import StrengthLevel
from pke.intervention.toast import toast_payload

_VALID_OUTCOMES = {"shown", "dismissed", "engaged", "bypassed"}

# Module-level embedder so the sentence-transformers model (or its hash
# fallback) is loaded once per process. /check is on the user's Send hot
# path; constructing a fresh Embedder on every request would add tens of
# milliseconds even for the in-memory variant.
_EMBEDDER = Embedder()


def router(store_getter: Any) -> APIRouter:
    """Build intervention API routes."""
    api = APIRouter(prefix="/api/v1/intervention")

    @api.post("/check")
    async def check(payload: dict[str, Any]) -> dict[str, Any]:
        app = store_getter()
        decider = PersistentInterventionDecider(
            sqlite=app.sqlite,
            llm_client=getattr(app, "llm_client", None),
        )

        # If the extension shipped the user's draft, try to resolve it to
        # a real skill node so the per-skill gate (gentle_every_n) and
        # the real unaided_mastery drive the decision. If nothing
        # matches above threshold we treat the prompt as a new topic and
        # return intervene=false rather than firing the card on an
        # ungrounded "unknown" skill — that's noisier than useful.
        prompt_text = payload.get("prompt_text")
        skill_id = payload.get("skill_id")
        skill_label = payload.get("skill_label")
        unaided_mastery_raw = payload.get("unaided_mastery")

        if isinstance(prompt_text, str) and prompt_text.strip() and not skill_id:
            match = resolve_prompt_to_skill(
                sqlite=app.sqlite,
                embedder=_EMBEDDER,
                prompt_text=prompt_text,
            )
            if match is None:
                return {"intervene": False}
            skill_id = match.skill_id
            skill_label = match.skill_label
            if unaided_mastery_raw is None:
                unaided_mastery_raw = match.unaided_mastery

        intervention = await decider.should_intervene_async(
            source=str(payload.get("source", "browser_ext")),
            skill_id=str(skill_id or "unknown"),
            skill_label=str(skill_label or "this skill"),
            unaided_mastery=float(unaided_mastery_raw if unaided_mastery_raw is not None else 0.5),
            task_type=str(payload.get("task_type", "learn")),
            context_summary=payload.get("context_summary") or prompt_text,
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
