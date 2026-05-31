"""FastAPI endpoint for the MV3 browser extension.

The endpoint accepts two payload shapes:

1. Legacy network-interception shape (older content scripts that
   monkey-patched ``window.fetch``)::

       {"url": ..., "reqBody": "...", "body": "...", "t0": ...,
        "kind": "stream"|"non_stream"|"stream_partial"}

2. DOM-observer shape used by ``pke-ext/content-main.js``::

       {"source": "browser_ext_chatgpt",
        "conversation_id": "<uuid>", "turn_index": 0,
        "user_text": "...", "assistant_text": "...",
        "user_message_id": "...", "assistant_message_id": "...",
        "t0": <ms epoch>, "url": "https://chatgpt.com/c/..."}

Both shapes flatten into the same ``EvidenceEvent`` (one user turn +
one assistant turn). The ``source`` field is honored if it starts with
``"browser_ext"``; otherwise the source defaults to ``"browser_ext"``
and is derived from the URL host. Allowed source values must appear in
``pke.evidence.models.VALID_SOURCES``.
"""

from __future__ import annotations

from typing import Any

from pke.evidence.models import (
    VALID_SOURCES,
    EvidenceEvent,
    EvidenceModality,
    EvidenceRole,
    EvidenceTurn,
    sha256_hex,
    utc_now,
)


def _coerce_source(payload: dict[str, Any], url: str) -> str:
    """Pick the canonical ``source`` for the event.

    Honors the payload's ``source`` field when it is a known
    ``browser_ext*`` value. Otherwise falls back to the generic
    ``"browser_ext"`` so older content scripts keep working.
    """
    requested = payload.get("source")
    if isinstance(requested, str) and requested.startswith("browser_ext"):
        if requested in VALID_SOURCES:
            return requested
        # Map URL-derived guesses for forward compatibility: an older
        # content script may send a ``browser_ext*`` source we haven't
        # registered yet. Keep these in lockstep with the manifest's
        # ``host_permissions``.
        if "chatgpt" in url or "chat.openai.com" in url:
            return "browser_ext_chatgpt" if "browser_ext_chatgpt" in VALID_SOURCES else "browser_ext"
        if "claude.ai" in url:
            return "browser_ext_claude_ai" if "browser_ext_claude_ai" in VALID_SOURCES else "browser_ext"
        if "gemini.google.com" in url:
            return "browser_ext_gemini" if "browser_ext_gemini" in VALID_SOURCES else "browser_ext"
    return "browser_ext"


def _coerce_texts(payload: dict[str, Any]) -> tuple[str, str]:
    """Extract (user_text, assistant_text) from either payload shape."""
    user_text = payload.get("user_text")
    assistant_text = payload.get("assistant_text")
    if user_text is not None or assistant_text is not None:
        return str(user_text or ""), str(assistant_text or "")
    # Legacy shape: reqBody / body (or request / response).
    request_body = payload.get("reqBody") or payload.get("request") or ""
    response_body = payload.get("body") or payload.get("response") or ""
    return str(request_body), str(response_body)


def event_from_browser_payload(payload: dict[str, Any]) -> EvidenceEvent:
    """Convert a browser-extension capture payload into an EvidenceEvent.

    See module docstring for the accepted payload shapes.
    """
    url = str(payload.get("url") or "")
    source = _coerce_source(payload, url)

    if "claude.ai" in url:
        app = "claude_web"
    elif "gemini.google.com" in url:
        app = "gemini_web"
    else:
        app = "chatgpt_web"

    user_text, assistant_text = _coerce_texts(payload)
    conv_id_raw = payload.get("conversation_id")
    conv_id = (
        str(conv_id_raw)
        if conv_id_raw
        else sha256_hex(url + user_text)[:16]
    )
    if not conv_id.startswith(("chatgpt_", "claude_web_", "gemini_")):
        conv_id = f"{app}_{conv_id}"
    turn_index = int(payload.get("turn_index") or 0)
    tags = ["partial"] if str(payload.get("kind", "")).endswith("partial") else []

    t0_raw = payload.get("t0")
    if t0_raw is None:
        occurred_at = utc_now()
    else:
        # ``t0`` is JS ``Date.now()`` (milliseconds since epoch).
        occurred_at = float(t0_raw) / 1000.0

    user_message_id = payload.get("user_message_id")
    assistant_message_id = payload.get("assistant_message_id")
    external_seed_parts = [conv_id, str(turn_index)]
    if user_message_id or assistant_message_id:
        external_seed_parts.append(str(user_message_id or ""))
        external_seed_parts.append(str(assistant_message_id or ""))
    else:
        external_seed_parts.append(user_text)
        external_seed_parts.append(assistant_text)
    external_id = sha256_hex(":".join(external_seed_parts))

    extra = {"url": url, "status": str(payload.get("status", ""))}
    if user_message_id:
        extra["user_message_id"] = str(user_message_id)
    if assistant_message_id:
        extra["assistant_message_id"] = str(assistant_message_id)

    return EvidenceEvent(
        source=source,
        external_id=external_id,
        conversation_id=conv_id,
        turn_index=turn_index,
        occurred_at=occurred_at,
        ingested_at=utc_now(),
        turns=[
            EvidenceTurn(
                role=EvidenceRole.USER, modality=EvidenceModality.TEXT, content=user_text
            ),
            EvidenceTurn(
                role=EvidenceRole.ASSISTANT,
                modality=EvidenceModality.TEXT,
                content=assistant_text,
            ),
        ],
        app=app,
        model=str(payload.get("model")) if payload.get("model") else None,
        user_agent=str(payload.get("user_agent")) if payload.get("user_agent") else None,
        tags=tags,
        extra=extra,
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
