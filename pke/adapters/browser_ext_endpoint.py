"""FastAPI endpoint for the MV3 browser extension."""

from __future__ import annotations

from typing import Any

from pke.evidence.models import (
    EvidenceEvent,
    EvidenceModality,
    EvidenceRole,
    EvidenceTurn,
    sha256_hex,
    utc_now,
)


def event_from_browser_payload(payload: dict[str, Any]) -> EvidenceEvent:
    """Convert a browser-extension capture payload into EvidenceEvent."""
    url = str(payload.get("url") or "")
    app = "chatgpt_web"
    if "claude.ai" in url:
        app = "claude_web"
    elif "gemini.google.com" in url:
        app = "gemini_web"
    request_body = payload.get("reqBody") or payload.get("request") or ""
    response_body = payload.get("body") or payload.get("response") or ""
    conv_id = str(payload.get("conversation_id") or sha256_hex(url + str(request_body))[:16])
    turn_index = int(payload.get("turn_index") or 0)
    tags = ["partial"] if str(payload.get("kind", "")).endswith("partial") else []
    return EvidenceEvent(
        source="browser_ext",
        external_id=sha256_hex(f"{conv_id}:{turn_index}:{request_body}:{response_body}"),
        conversation_id=conv_id
        if conv_id.startswith(("chatgpt_", "claude_web_", "gemini_"))
        else f"{app}_{conv_id}",
        turn_index=turn_index,
        occurred_at=float(payload.get("t0", utc_now() * 1000)) / 1000,
        ingested_at=utc_now(),
        turns=[
            EvidenceTurn(
                role=EvidenceRole.USER, modality=EvidenceModality.TEXT, content=str(request_body)
            ),
            EvidenceTurn(
                role=EvidenceRole.ASSISTANT,
                modality=EvidenceModality.TEXT,
                content=str(response_body),
            ),
        ],
        app=app,
        model=str(payload.get("model")) if payload.get("model") else None,
        user_agent=str(payload.get("user_agent")) if payload.get("user_agent") else None,
        tags=tags,
        extra={"url": url, "status": str(payload.get("status", ""))},
    )


def router(store_getter: Any) -> Any:
    """Build a FastAPI router for extension ingestion."""
    from fastapi import APIRouter

    api = APIRouter()

    @api.post("/api/v1/evidence")
    async def post_evidence(payload: dict[str, Any]) -> dict[str, Any]:
        event = event_from_browser_payload(payload)
        result = store_getter().evidence.add(event)
        return {"status": result.status, "id": result.evidence_id}

    @api.post("/internal/adapters/claude_code_hook/ingest")
    async def post_hook(payload: dict[str, Any]) -> dict[str, Any]:
        from pke.adapters.claude_code_hook import event_from_hook_envelope

        result = store_getter().evidence.add(event_from_hook_envelope(payload))
        return {"status": result.status, "id": result.evidence_id}

    @api.post("/evidence/ingest")
    async def legacy_ingest(payload: dict[str, Any]) -> dict[str, Any]:
        return await post_evidence(payload)

    @api.get("/api/v1/extension/status")
    async def status() -> dict[str, Any]:
        return {"server": "reachable"}

    return api
