"""Anthropic Messages API passive proxy helpers.

The proxy forwards traffic verbatim to ``api.anthropic.com`` (or any
configured upstream) and captures evidence on the way through. The
Anthropic Messages SSE stream is rebroadcast to the client chunk by
chunk while a buffered copy is kept for evidence assembly after the
stream finishes.

Anthropic's SSE wire format uses paired ``event:`` and ``data:`` lines.
For text reassembly we care about ``content_block_delta`` events whose
``delta.type == "text_delta"`` (concatenate ``delta.text``). Other event
types (``message_start``, ``content_block_start``, ``message_delta``,
``ping``, ``content_block_stop``, ``message_stop``) are ignored for
text-only evidence. There is no ``[DONE]`` sentinel; ``message_stop``
ends the stream.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import Request

from pke.evidence.models import (
    EvidenceEvent,
    EvidenceModality,
    EvidenceRole,
    EvidenceTurn,
    sha256_hex,
    utc_now,
)

# Hop-by-hop headers describe an encoding of the upstream body that no
# longer applies once we rechunk through StreamingResponse.
_HOP_BY_HOP_HEADERS = {
    "content-length",
    "transfer-encoding",
    "content-encoding",
    "connection",
}


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


def reassemble_anthropic_sse(raw_body: bytes) -> str:
    """Concatenate ``text_delta`` fragments from an Anthropic SSE body.

    The reassembly walks ``data:`` lines, decodes each JSON payload, and
    appends ``delta.text`` whenever the event is a
    ``content_block_delta`` carrying a ``text_delta`` delta. Any other
    event type (including ``ping``, ``input_json_delta`` on tool_use
    blocks, ``message_start``, ``message_stop``) is ignored.
    """
    text_parts: list[str] = []
    for line in raw_body.decode("utf-8", errors="ignore").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "content_block_delta":
            continue
        delta = event.get("delta")
        if not isinstance(delta, dict):
            continue
        if delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str):
                text_parts.append(text)
    return "".join(text_parts)


def create_proxy_app(
    store_getter: Any,
    *,
    upstream: str = "https://api.anthropic.com",
    client_factory: Any = None,
) -> Any:
    """Create a FastAPI app that observes Anthropic Messages traffic.

    SSE responses are streamed back unchanged so the client (Claude Code,
    the official SDK, etc.) sees deltas with no extra latency; a buffered
    copy is used to build the evidence event after the stream completes,
    inside a background task.

    ``client_factory`` is a zero-arg callable returning a fresh
    :class:`httpx.AsyncClient`; defaults to ``httpx.AsyncClient(timeout=None)``.
    """
    from fastapi import FastAPI
    from fastapi.responses import Response, StreamingResponse
    from starlette.background import BackgroundTask

    if client_factory is None:

        def client_factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(timeout=None)

    app = FastAPI()

    def _record_evidence(
        *, body: bytes, response_bytes: bytes, request_id: str | None, is_sse: bool
    ) -> None:
        try:
            request_json = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if is_sse:
            response_text = reassemble_anthropic_sse(response_bytes)
        else:
            try:
                response_text = response_bytes.decode("utf-8")
            except UnicodeDecodeError:
                response_text = response_bytes.decode("utf-8", errors="replace")
        store_getter().evidence.add(
            event_from_anthropic_request_response(
                request_body=request_json,
                response_text=response_text,
                request_id=request_id,
            )
        )

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        response_model=None,
    )
    async def proxy(path: str, request: Request) -> Any:
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        target_url = f"{upstream.rstrip('/')}/{path}"

        client = client_factory()
        upstream_req = client.build_request(
            request.method, target_url, content=body, headers=headers
        )
        upstream_response = await client.send(upstream_req, stream=True)

        capture = request.method == "POST" and path == "v1/messages"
        content_type = upstream_response.headers.get("content-type", "")
        is_sse = content_type.startswith("text/event-stream")

        response_headers = {
            k: v
            for k, v in upstream_response.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        request_id = upstream_response.headers.get("request-id")

        if is_sse:
            buffer: list[bytes] = []

            async def streamer() -> Any:
                try:
                    async for chunk in upstream_response.aiter_raw():
                        if capture:
                            buffer.append(chunk)
                        yield chunk
                finally:
                    await upstream_response.aclose()
                    await client.aclose()

            async def finalize() -> None:
                if not capture:
                    return
                try:
                    _record_evidence(
                        body=body,
                        response_bytes=b"".join(buffer),
                        request_id=request_id,
                        is_sse=True,
                    )
                except Exception:
                    import logging

                    logging.getLogger(__name__).exception("anthropic_proxy evidence capture failed")

            return StreamingResponse(
                streamer(),
                status_code=upstream_response.status_code,
                headers=response_headers,
                media_type=content_type or None,
                background=BackgroundTask(finalize),
            )

        try:
            response_bytes = await upstream_response.aread()
        finally:
            await upstream_response.aclose()
            await client.aclose()

        if capture:
            try:
                _record_evidence(
                    body=body,
                    response_bytes=response_bytes,
                    request_id=request_id,
                    is_sse=False,
                )
            except Exception:
                import logging

                logging.getLogger(__name__).exception("anthropic_proxy evidence capture failed")

        return Response(
            content=response_bytes,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=content_type or None,
        )

    return app
