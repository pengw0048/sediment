"""OpenAI-compatible passive HTTP proxy helpers.

The proxy forwards traffic verbatim to the configured upstream and
captures evidence on the way through. Streaming Server-Sent Event (SSE)
responses are streamed back to the caller chunk by chunk so the client
sees tokens as soon as the upstream emits them; a second copy of the
chunks is buffered in-memory so an :class:`EvidenceEvent` can be built
after the stream completes.

Two upstream endpoints are captured for OpenAI today:

* ``POST /v1/chat/completions`` — chunks have a single ``data: <json>``
  line per event; ``choices[0].delta.content`` carries text fragments;
  ``data: [DONE]`` terminates the stream.
* ``POST /v1/responses`` — chunks use ``response.output_text.delta``
  events with ``.delta`` strings.

Non-streaming JSON responses are still buffered the old way.
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
# longer applies once we rechunk through StreamingResponse. Forwarding
# them confuses both clients and intermediate proxies.
_HOP_BY_HOP_HEADERS = {
    "content-length",
    "transfer-encoding",
    "content-encoding",
    "connection",
}


def event_from_openai_request_response(
    *, request_body: dict[str, Any], response_text: str, request_id: str | None = None
) -> EvidenceEvent:
    """Build evidence from an OpenAI chat/responses request and response text."""
    messages = request_body.get("messages", [])
    user_content = ""
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                user_content = json.dumps(message.get("content"), ensure_ascii=False)
                break
    if not user_content:
        user_content = json.dumps(request_body, ensure_ascii=False)
    first = user_content[:1024]
    conversation_id = f"openai_proxy_{sha256_hex(first)[:16]}"
    external_seed = (
        request_id or f"{utc_now()}:{sha256_hex(json.dumps(request_body, sort_keys=True))}"
    )
    return EvidenceEvent(
        source="openai_proxy",
        external_id=sha256_hex(external_seed),
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
        app="openai_api",
        model=str(request_body.get("model")) if request_body.get("model") else None,
    )


def reassemble_openai_sse(raw_body: bytes, *, path: str) -> str:
    """Concatenate text fragments out of an OpenAI SSE response body.

    ``path`` selects the event taxonomy:

    * ``v1/chat/completions`` reads ``choices[0].delta.content`` for
      natural-language deltas and ``choices[0].delta.tool_calls[i]``
      for function-call deltas (``name`` arrives once, ``arguments``
      accumulates across chunks).
    * ``v1/responses`` reads ``delta`` from
      ``response.output_text.delta`` events for text, and pairs
      ``response.output_item.added`` (carrying a function ``name``)
      with ``response.function_call_arguments.delta`` (carrying the
      JSON-string arguments fragment) for tool calls.

    Tool-call deltas are reassembled into a compact
    ``[tool_call name(arguments)]`` line appended after the body
    text so evidence captures sessions where the model emitted only
    a function call (no plain text), which the old reassembler
    silently dropped.

    Lines that don't begin with ``data:``, the ``[DONE]`` sentinel,
    and JSON that fails to parse are skipped — the goal is best-effort
    reassembly for evidence, not strict validation.
    """
    text_parts: list[str] = []
    # tool_parts is keyed by tool-call slot (chat-completions uses
    # the integer ``index`` on each delta; responses uses the
    # ``output_index`` or ``item_id`` on the streaming events). Each
    # slot accumulates ``name`` once and ``arguments`` as a string.
    tool_parts: dict[object, dict[str, str]] = {}
    is_responses = path.endswith("v1/responses")
    for line in raw_body.decode("utf-8", errors="ignore").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if is_responses:
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            elif event_type == "response.output_item.added":
                item = event.get("item") or {}
                if isinstance(item, dict) and item.get("type") == "function_call":
                    # Prefer ``output_index`` (stable across all
                    # later argument deltas for this item) and fall
                    # back to the item id when the upstream omits it.
                    slot = event.get("output_index")
                    if slot is None:
                        slot = item.get("id")
                    bucket = tool_parts.setdefault(
                        slot, {"name": "", "arguments": ""}
                    )
                    name = item.get("name")
                    if isinstance(name, str):
                        bucket["name"] = name
            elif event_type == "response.function_call_arguments.delta":
                slot = event.get("output_index")
                if slot is None:
                    slot = event.get("item_id")
                delta = event.get("delta")
                if isinstance(delta, str):
                    bucket = tool_parts.setdefault(
                        slot, {"name": "", "arguments": ""}
                    )
                    bucket["arguments"] += delta
            continue
        choices = event.get("choices") or []
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    # Each chunk carries an ``index`` so fragments
                    # land in the right slot even when the model
                    # emits multiple parallel tool calls. Default to
                    # 0 if the upstream omits it (legacy behavior).
                    idx = tc.get("index", 0)
                    bucket = tool_parts.setdefault(
                        idx, {"name": "", "arguments": ""}
                    )
                    func = tc.get("function")
                    if isinstance(func, dict):
                        name = func.get("name")
                        if isinstance(name, str) and name:
                            bucket["name"] = name
                        args = func.get("arguments")
                        if isinstance(args, str):
                            bucket["arguments"] += args
    body = "".join(text_parts)
    if tool_parts:
        # Sort slots deterministically; chat-completions uses ints,
        # responses uses ints or strings, so coerce both to str for
        # a stable order.
        tail = "".join(
            f"\n[tool_call {tool_parts[slot].get('name') or '?'}"
            f"({tool_parts[slot].get('arguments') or ''})]"
            for slot in sorted(tool_parts, key=lambda k: str(k))
        )
        body = body + tail if body else tail.lstrip("\n")
    return body


def create_proxy_app(
    store_getter: Any,
    *,
    upstream: str = "https://api.openai.com",
    client_factory: Any = None,
) -> Any:
    """Create a FastAPI app that passively observes OpenAI-compatible requests.

    Streaming SSE responses are forwarded chunk by chunk via
    :class:`StreamingResponse` so the client sees tokens with no extra
    latency; a buffered copy of the bytes is used to build the evidence
    event after the stream finishes, inside a background task.

    ``client_factory`` is a zero-arg callable that returns a fresh
    :class:`httpx.AsyncClient`. The default constructs
    ``httpx.AsyncClient(timeout=None)`` — tests inject a factory that
    binds an :class:`httpx.MockTransport`.
    """
    from fastapi import FastAPI
    from fastapi.responses import Response, StreamingResponse
    from starlette.background import BackgroundTask

    if client_factory is None:

        def client_factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(timeout=None)

    app = FastAPI()

    capture_paths = {"v1/chat/completions", "v1/responses"}

    def _record_evidence(
        *,
        path: str,
        body: bytes,
        response_bytes: bytes,
        request_id: str | None,
        is_sse: bool,
    ) -> None:
        try:
            request_json = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if is_sse:
            response_text = reassemble_openai_sse(response_bytes, path=path)
        else:
            try:
                response_text = response_bytes.decode("utf-8")
            except UnicodeDecodeError:
                response_text = response_bytes.decode("utf-8", errors="replace")
        event = event_from_openai_request_response(
            request_body=request_json,
            response_text=response_text,
            request_id=request_id,
        )
        store_getter().evidence.add(event)

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

        capture = request.method == "POST" and path in capture_paths
        content_type = upstream_response.headers.get("content-type", "")
        is_sse = content_type.startswith("text/event-stream")

        response_headers = {
            k: v
            for k, v in upstream_response.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        request_id = upstream_response.headers.get("x-request-id")

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
                        path=path,
                        body=body,
                        response_bytes=b"".join(buffer),
                        request_id=request_id,
                        is_sse=True,
                    )
                except Exception:
                    # Evidence capture is best-effort; it must not
                    # surface as a client error after the stream is
                    # already on its way to the caller.
                    import logging

                    logging.getLogger(__name__).exception("openai_proxy evidence capture failed")

            return StreamingResponse(
                streamer(),
                status_code=upstream_response.status_code,
                headers=response_headers,
                media_type=content_type or None,
                background=BackgroundTask(finalize),
            )

        # Non-streaming path: drain into memory and return one Response.
        try:
            response_bytes = await upstream_response.aread()
        finally:
            await upstream_response.aclose()
            await client.aclose()

        if capture:
            try:
                _record_evidence(
                    path=path,
                    body=body,
                    response_bytes=response_bytes,
                    request_id=request_id,
                    is_sse=False,
                )
            except Exception:
                import logging

                logging.getLogger(__name__).exception("openai_proxy evidence capture failed")

        return Response(
            content=response_bytes,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=content_type or None,
        )

    return app
