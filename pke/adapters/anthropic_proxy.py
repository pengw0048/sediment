"""Anthropic Messages API passive proxy helpers."""

from __future__ import annotations

import json
from typing import Any

from pke.evidence.models import (
    EvidenceEvent,
    EvidenceModality,
    EvidenceRole,
    EvidenceTurn,
    sha256_hex,
    utc_now,
)


def event_from_anthropic_request_response(
    *, request_body: dict[str, Any], response_text: str, request_id: str | None = None
) -> EvidenceEvent:
    """Build evidence from an Anthropic Messages request and response."""
    messages = request_body.get("messages", [])
    user_content = ""
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                user_content = json.dumps(message.get("content"), ensure_ascii=False)
                break
    conversation_id = f"anthropic_proxy_{sha256_hex(user_content[:1024])[:16]}"
    return EvidenceEvent(
        source="anthropic_proxy",
        external_id=sha256_hex(
            request_id or json.dumps(request_body, sort_keys=True) + response_text[:128]
        ),
        conversation_id=conversation_id,
        turn_index=0,
        occurred_at=utc_now(),
        ingested_at=utc_now(),
        turns=[
            EvidenceTurn(
                role=EvidenceRole.USER, modality=EvidenceModality.TEXT, content=user_content
            ),
            EvidenceTurn(
                role=EvidenceRole.ASSISTANT, modality=EvidenceModality.TEXT, content=response_text
            ),
        ],
        app="claude_code",
        model=str(request_body.get("model")) if request_body.get("model") else None,
    )


def create_proxy_app(store_getter: Any, *, upstream: str = "https://api.anthropic.com") -> Any:
    """Create a FastAPI app that observes Anthropic Messages traffic."""
    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import Response

    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request) -> Response:
        body = await request.body()
        headers = dict(request.headers)
        async with httpx.AsyncClient(timeout=None) as client:
            upstream_response = await client.request(
                request.method,
                f"{upstream.rstrip('/')}/{path}",
                content=body,
                headers={k: v for k, v in headers.items() if k.lower() != "host"},
            )
        if request.method == "POST" and path == "v1/messages":
            try:
                request_json = json.loads(body.decode("utf-8"))
                store_getter().evidence.add(
                    event_from_anthropic_request_response(
                        request_body=request_json,
                        response_text=upstream_response.text,
                        request_id=upstream_response.headers.get("request-id"),
                    )
                )
            except Exception:
                pass
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=dict(upstream_response.headers),
        )

    return app
