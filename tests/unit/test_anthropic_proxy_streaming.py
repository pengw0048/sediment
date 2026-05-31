"""End-to-end tests for the Anthropic proxy's SSE pass-through path.

Same shape as the OpenAI proxy tests: a real uvicorn server hosts the
proxy app, and ``create_proxy_app`` is given an explicit
``client_factory`` so the proxy's upstream calls hit an
:class:`httpx.MockTransport` while the test's outgoing client uses
normal HTTP to the loopback server.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import uvicorn

from pke.adapters import anthropic_proxy


def _wait_for_rows(app: object, *, expected: int, timeout: float = 5.0) -> list[object]:
    """Poll the store until ``expected`` rows are present or ``timeout`` elapses."""
    deadline = time.time() + timeout
    rows: list[object] = []
    while time.time() < deadline:
        rows = list(app.evidence.list())  # type: ignore[attr-defined]
        if len(rows) >= expected:
            return rows
        time.sleep(0.02)
    return rows


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _serve(app: object, port: int) -> Iterator[None]:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="critical",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    errors: list[BaseException] = []

    def _target() -> None:
        try:
            server.run()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    deadline = time.time() + 10.0
    while time.time() < deadline and not server.started:
        if errors:
            raise RuntimeError(f"uvicorn crashed: {errors[0]!r}")
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("uvicorn did not start in time")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def _make_factory(transport: httpx.MockTransport) -> tuple[object, list[httpx.Request]]:
    """Build a zero-arg ``client_factory`` for ``create_proxy_app``."""
    seen: list[httpx.Request] = []

    def factory() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=transport, timeout=None)
        original_send = client.send

        async def tracking_send(request: httpx.Request, **send_kwargs: object) -> httpx.Response:
            seen.append(request)
            return await original_send(request, **send_kwargs)

        client.send = tracking_send  # type: ignore[method-assign]
        return client

    return factory, seen


def _build_anthropic_sse(text_chunks: list[str]) -> list[bytes]:
    """Render a minimal Anthropic Messages SSE stream that emits ``text_chunks``."""
    events: list[bytes] = []
    events.append(
        b"event: message_start\n"
        b'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-test"}}\n\n'
    )
    events.append(
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    )
    for chunk in text_chunks:
        payload = json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": chunk},
            }
        )
        events.append(f"event: content_block_delta\ndata: {payload}\n\n".encode())
    events.append(b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n')
    events.append(
        b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
    )
    events.append(b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
    return events


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_anthropic_sse_streams_each_chunk_and_records_one_event(app: object) -> None:
    """The Anthropic SSE response is rebroadcast chunk-by-chunk and captured."""
    text_chunks = ["Hi", " there", "!"]
    events = _build_anthropic_sse(text_chunks)
    proceed = asyncio.Event()

    async def streamed_body() -> AsyncIterator[bytes]:
        for chunk in events:
            yield chunk
            await proceed.wait()
            proceed.clear()

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "request-id": "req-anth"},
            content=streamed_body(),
        )

    transport = httpx.MockTransport(upstream_handler)
    factory, seen = _make_factory(transport)

    proxy_app = anthropic_proxy.create_proxy_app(
        lambda: app, upstream="https://api.example.com", client_factory=factory
    )

    request_body = {
        "model": "claude-test",
        "stream": True,
        "messages": [{"role": "user", "content": "ping"}],
    }
    received_chunks: list[bytes] = []
    received_headers: dict[str, str] = {}

    port = _free_port()
    with _serve(proxy_app, port):

        async def drive() -> None:
            async with (
                httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client,
                client.stream(
                    "POST",
                    "/v1/messages",
                    content=json.dumps(request_body).encode(),
                    headers={"content-type": "application/json"},
                    timeout=10.0,
                ) as response,
            ):
                assert response.status_code == 200
                received_headers.update({k.lower(): v for k, v in response.headers.items()})
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    received_chunks.append(chunk)
                    # Forward progress on the client side proves the
                    # proxy isn't buffering — the upstream is paused
                    # in ``proceed.wait()`` between each chunk.
                    proceed.set()
                proceed.set()

        asyncio.run(drive())
        rows = _wait_for_rows(app, expected=1)

    assert "content-length" not in received_headers
    assert received_headers.get("content-type", "").startswith("text/event-stream")

    full_body = b"".join(received_chunks)
    for chunk in text_chunks:
        assert chunk.encode() in full_body
    assert b"message_stop" in full_body
    assert len(received_chunks) >= 2

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/messages"

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "anthropic_proxy"
    # Assembled assistant text is the concatenation of every text_delta.
    assistant = "".join(text_chunks)
    assert assistant in row["content"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_anthropic_non_streaming_response_falls_back_to_buffered_capture(app: object) -> None:
    """JSON (non-SSE) responses must still produce evidence."""
    response_payload = {
        "id": "msg_2",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "buffered reply"}],
        "model": "claude-test",
    }
    response_body = json.dumps(response_payload).encode()

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=response_body,
        )

    transport = httpx.MockTransport(upstream_handler)
    factory, _ = _make_factory(transport)
    proxy_app = anthropic_proxy.create_proxy_app(
        lambda: app, upstream="https://api.example.com", client_factory=factory
    )

    port = _free_port()
    with _serve(proxy_app, port):

        async def drive() -> httpx.Response:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                return await client.post(
                    "/v1/messages",
                    content=json.dumps(
                        {"model": "claude-test", "messages": [{"role": "user", "content": "hi"}]}
                    ).encode(),
                    headers={"content-type": "application/json"},
                    timeout=10.0,
                )

        response = asyncio.run(drive())
        rows = _wait_for_rows(app, expected=1)

    assert response.status_code == 200
    assert json.loads(response.content) == response_payload
    assert len(rows) == 1
    assert "buffered reply" in rows[0]["content"]


def test_reassemble_anthropic_sse_concatenates_text_deltas() -> None:
    """Direct coverage on the SSE reassembler."""
    body = (
        b"event: message_start\n"
        b'data: {"type":"message_start","message":{}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"foo"}}\n\n'
        b"event: ping\n"
        b'data: {"type":"ping"}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"bar"}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{}"}}\n\n'
        b"event: message_stop\n"
        b'data: {"type":"message_stop"}\n\n'
    )
    assert anthropic_proxy.reassemble_anthropic_sse(body) == "foobar"
