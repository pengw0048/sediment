"""End-to-end tests for the OpenAI proxy's SSE pass-through path.

These tests run the proxy app on a real uvicorn server bound to a random
loopback port. That's necessary because :class:`httpx.ASGITransport`
buffers the full response body in memory before returning, which would
mask whether the proxy is actually streaming or just collecting bytes.

The "fake upstream" is provided by patching ``httpx.AsyncClient`` inside
the adapter module so the proxy talks to an :class:`httpx.MockTransport`
backed by a hand-written SSE handler.
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

from pke.adapters import openai_proxy


def _wait_for_rows(app: object, *, expected: int, timeout: float = 5.0) -> list[object]:
    """Poll ``app.evidence.list()`` until at least ``expected`` rows are present.

    The proxy records evidence from a Starlette ``BackgroundTask`` that
    fires after the response body has finished streaming. The test
    must briefly wait for that side-effect.
    """
    deadline = time.time() + timeout
    rows: list[object] = []
    while time.time() < deadline:
        rows = list(app.evidence.list())  # type: ignore[attr-defined]
        if len(rows) >= expected:
            return rows
        time.sleep(0.02)
    return rows


def _free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _serve(app: object, port: int) -> Iterator[None]:
    """Run ``app`` on uvicorn in a background thread for the duration of the block.

    ``install_signal_handlers=False`` is required because uvicorn would
    otherwise call ``signal.signal`` from a non-main thread, which
    raises ``ValueError``.
    """
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
            raise RuntimeError(f"uvicorn crashed at startup: {errors[0]!r}")
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError(
            f"uvicorn did not start in time (errors={errors!r}, " f"started={server.started})"
        )
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def _build_chunks(deltas: list[str]) -> list[bytes]:
    """Render an SSE byte stream that looks like OpenAI chat.completions."""
    chunks: list[bytes] = []
    for delta in deltas:
        body = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {"content": delta}, "index": 0}],
        }
        chunks.append(f"data: {json.dumps(body)}\n\n".encode())
    chunks.append(b"data: [DONE]\n\n")
    return chunks


def _make_factory(
    transport: httpx.MockTransport,
) -> tuple[object, list[httpx.Request]]:
    """Build a ``client_factory`` for ``create_proxy_app`` that uses ``transport``.

    Returns ``(factory, seen)`` where ``seen`` records every request the
    proxy forwards upstream.
    """
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


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_openai_sse_streams_each_chunk_and_records_one_event(app: object) -> None:
    """Verify chunk-by-chunk forwarding plus a single reassembled event.

    The upstream handler emits SSE chunks one at a time, awaiting
    confirmation from the client between chunks. If the proxy were
    buffering the full body, the upstream would deadlock and the test
    would time out — so successful completion is itself evidence of
    chunk-by-chunk pass-through.
    """
    deltas = ["Hello", ", ", "world", "!"]
    chunks_emitted: list[bytes] = _build_chunks(deltas)
    proceed = asyncio.Event()

    async def streamed_body() -> AsyncIterator[bytes]:
        for chunk in chunks_emitted:
            yield chunk
            await proceed.wait()
            proceed.clear()

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-request-id": "req-test"},
            content=streamed_body(),
        )

    transport = httpx.MockTransport(upstream_handler)
    factory, seen = _make_factory(transport)

    proxy_app = openai_proxy.create_proxy_app(
        lambda: app, upstream="https://api.example.com", client_factory=factory
    )

    request_body = {
        "model": "gpt-test",
        "stream": True,
        "messages": [{"role": "user", "content": "Say hi"}],
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
                    "/v1/chat/completions",
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
                    # Hand control back to the upstream side so it
                    # can emit the next chunk. The fact that the
                    # client side observes any forward progress at
                    # all proves the proxy is not buffering.
                    proceed.set()
                proceed.set()

        asyncio.run(drive())
        # Evidence is recorded inside a Starlette BackgroundTask. Wait
        # for it before tearing down the uvicorn server — otherwise
        # ``should_exit`` will preempt the task.
        rows = _wait_for_rows(app, expected=1)

    # Hop-by-hop headers are stripped because we rechunk through Starlette.
    assert "content-length" not in received_headers
    assert received_headers.get("content-type", "").startswith("text/event-stream")

    full_body = b"".join(received_chunks)
    for delta in deltas:
        assert delta.encode() in full_body
    assert b"[DONE]" in full_body
    # Multiple chunks observed on the client side rules out "buffered
    # then dumped at end" behavior even if the deadlock check above ever
    # regressed.
    assert len(received_chunks) >= 2

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/chat/completions"

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "openai_proxy"
    assistant_text = "".join(deltas)
    assert assistant_text in row["content"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_openai_responses_endpoint_reassembles_delta_events(app: object) -> None:
    """``/v1/responses`` uses a different SSE taxonomy; check we handle it."""
    deltas = ["foo", "bar", "baz"]
    events = [
        f"data: {json.dumps({'type': 'response.output_text.delta', 'delta': d})}\n\n".encode()
        for d in deltas
    ]
    events.append(b'data: {"type": "response.completed"}\n\n')

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            for chunk in events:
                yield chunk

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-request-id": "resp-1"},
            content=body(),
        )

    transport = httpx.MockTransport(upstream_handler)
    factory, _ = _make_factory(transport)

    proxy_app = openai_proxy.create_proxy_app(
        lambda: app, upstream="https://api.example.com", client_factory=factory
    )
    payload = json.dumps(
        {
            "model": "gpt-resp",
            "stream": True,
            "messages": [{"role": "user", "content": "Tell me"}],
        }
    ).encode()

    port = _free_port()
    with _serve(proxy_app, port):

        async def drive() -> None:
            async with (
                httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client,
                client.stream(
                    "POST",
                    "/v1/responses",
                    content=payload,
                    headers={"content-type": "application/json"},
                    timeout=10.0,
                ) as response,
            ):
                assert response.status_code == 200
                async for _ in response.aiter_bytes():
                    pass

        asyncio.run(drive())
        rows = _wait_for_rows(app, expected=1)

    assert len(rows) == 1
    assert "foobarbaz" in rows[0]["content"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_openai_non_streaming_response_falls_back_to_buffered_capture(app: object) -> None:
    """Plain JSON (non-SSE) responses must still produce evidence."""
    response_payload = {
        "id": "chatcmpl-2",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "buffered hello"}}],
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

    proxy_app = openai_proxy.create_proxy_app(
        lambda: app, upstream="https://api.example.com", client_factory=factory
    )

    port = _free_port()
    with _serve(proxy_app, port):

        async def drive() -> httpx.Response:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                return await client.post(
                    "/v1/chat/completions",
                    content=json.dumps(
                        {"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]}
                    ).encode(),
                    headers={"content-type": "application/json"},
                    timeout=10.0,
                )

        response = asyncio.run(drive())
        rows = _wait_for_rows(app, expected=1)

    assert response.status_code == 200
    assert json.loads(response.content) == response_payload

    assert len(rows) == 1
    # Non-streaming path stores the raw JSON response text in the
    # assistant turn — confirm the assistant content survives the round
    # trip into the flattened evidence row.
    assert response_payload["choices"][0]["message"]["content"] in rows[0]["content"]


def test_reassemble_openai_sse_handles_done_sentinel_and_bad_json() -> None:
    """Direct unit coverage on the SSE reassembler helper."""
    body = (
        b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
        b"data: not-json\n\n"
        b"data: [DONE]\n\n"
        b'data: {"choices": [{"delta": {"content": " there"}}]}\n\n'
    )
    assert openai_proxy.reassemble_openai_sse(body, path="v1/chat/completions") == "hi there"


def test_reassemble_openai_sse_responses_taxonomy() -> None:
    body = (
        b'data: {"type": "response.output_text.delta", "delta": "ab"}\n\n'
        b'data: {"type": "response.output_text.delta", "delta": "cd"}\n\n'
        b'data: {"type": "response.completed"}\n\n'
    )
    assert openai_proxy.reassemble_openai_sse(body, path="v1/responses") == "abcd"


def test_reassemble_openai_sse_natural_language_text_only() -> None:
    """The text wrapper returns only the assistant's natural-language deltas.

    Function-call deltas travel via the structured wrapper; the
    text-only reassembler ignores them.
    """
    body = (
        b'data: {"choices": [{"delta": {"content": "I will look this up. "}}]}\n\n'
        b'data: {"choices": [{"delta": {"tool_calls": ['
        b'{"index": 0, "function": {"name": "get_weather", "arguments": "{\\"city\\":"}}]}}]}\n\n'
        b'data: {"choices": [{"delta": {"tool_calls": ['
        b'{"index": 0, "function": {"arguments": "\\"Tokyo\\"}"}}]}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    out = openai_proxy.reassemble_openai_sse(body, path="v1/chat/completions")
    assert out == "I will look this up. "
    assert "[tool_call" not in out


def test_reassemble_openai_sse_responses_taxonomy_natural_language_only() -> None:
    """The responses taxonomy reassembles ``output_text.delta`` into the text body.

    ``response.output_item.added`` + ``response.function_call_arguments.delta``
    deltas feed the structured wrapper but never the text body.
    """
    body = (
        b'data: {"type": "response.output_text.delta", "delta": "thinking..."}\n\n'
        b'data: {"type": "response.output_item.added", "output_index": 0, '
        b'"item": {"type": "function_call", "name": "get_weather"}}\n\n'
        b'data: {"type": "response.function_call_arguments.delta", '
        b'"output_index": 0, "delta": "{\\"city\\":"}\n\n'
        b'data: {"type": "response.function_call_arguments.delta", '
        b'"output_index": 0, "delta": "\\"Tokyo\\"}"}\n\n'
        b'data: {"type": "response.completed"}\n\n'
    )
    out = openai_proxy.reassemble_openai_sse(body, path="v1/responses")
    assert out == "thinking..."
    assert "[tool_call" not in out


def test_reassemble_openai_sse_tool_call_only_returns_empty_text() -> None:
    """A session with only a tool call (no natural-language deltas) returns empty text.

    The tool call lives in the structured wrapper; the text-only path
    is honest about there being no assistant prose.
    """
    body = (
        b'data: {"choices": [{"delta": {"tool_calls": ['
        b'{"index": 0, "function": {"name": "lookup", "arguments": "{\\"id\\": 42}"}}]}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    out = openai_proxy.reassemble_openai_sse(body, path="v1/chat/completions")
    assert out == ""


def test_reassemble_with_tool_calls_returns_structured_records() -> None:
    """The structured helper returns the natural-language text and a populated record list."""
    body = (
        b'data: {"choices": [{"delta": {"content": "I will look this up. "}}]}\n\n'
        b'data: {"choices": [{"delta": {"tool_calls": ['
        b'{"index": 0, "function": {"name": "get_weather", "arguments": "{\\"city\\":"}}]}}]}\n\n'
        b'data: {"choices": [{"delta": {"tool_calls": ['
        b'{"index": 0, "function": {"arguments": "\\"Tokyo\\"}"}}]}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    text, tool_calls = openai_proxy.reassemble_openai_sse_with_tool_calls(
        body, path="v1/chat/completions"
    )
    assert text == "I will look this up. "
    assert "[tool_call" not in text
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "get_weather"
    assert tool_calls[0].arguments == '{"city":"Tokyo"}'


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_openai_sse_tool_call_stream_populates_event_tool_calls_field(app: object) -> None:
    """End-to-end: a tool_call SSE stream lands as a structured field on the event.

    The captured evidence row's ``metadata_json`` carries a non-empty
    ``tool_calls`` list. The assistant content is only the
    natural-language deltas; tool-call metadata lives exclusively in
    the structured field.
    """
    chunks = [
        b'data: {"choices": [{"delta": {"content": "Checking. "}}]}\n\n',
        (
            b'data: {"choices": [{"delta": {"tool_calls": ['
            b'{"index": 0, "function": {"name": "get_weather", "arguments": "{\\"city\\":"}}]}}]}\n\n'
        ),
        (
            b'data: {"choices": [{"delta": {"tool_calls": ['
            b'{"index": 0, "function": {"arguments": "\\"Tokyo\\"}"}}]}}]}\n\n'
        ),
        b"data: [DONE]\n\n",
    ]

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-request-id": "tc-1"},
            content=body(),
        )

    transport = httpx.MockTransport(upstream_handler)
    factory, _ = _make_factory(transport)
    proxy_app = openai_proxy.create_proxy_app(
        lambda: app, upstream="https://api.example.com", client_factory=factory
    )

    payload = json.dumps(
        {
            "model": "gpt-test",
            "stream": True,
            "messages": [{"role": "user", "content": "weather?"}],
        }
    ).encode()

    port = _free_port()
    with _serve(proxy_app, port):

        async def drive() -> None:
            async with (
                httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client,
                client.stream(
                    "POST",
                    "/v1/chat/completions",
                    content=payload,
                    headers={"content-type": "application/json"},
                    timeout=10.0,
                ) as response,
            ):
                async for _ in response.aiter_bytes():
                    pass

        asyncio.run(drive())
        rows = _wait_for_rows(app, expected=1)

    assert len(rows) == 1
    row = rows[0]
    assert "Checking." in row["content"]
    assert "[tool_call" not in row["content"]
    # Structured records survive the round-trip through metadata_json.
    metadata = json.loads(row["metadata_json"])
    assert metadata.get("tool_calls"), "tool_calls must be a populated list"
    assert metadata["tool_calls"][0]["name"] == "get_weather"
    assert metadata["tool_calls"][0]["arguments"] == '{"city":"Tokyo"}'
