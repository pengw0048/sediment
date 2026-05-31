"""Integration test for the browser extension's evidence endpoint.

The MV3 content script POSTs a DOM-observer payload to
``/api/v1/evidence`` and the evidence should show up in the store via
the same path the live daemon uses.

This test drives the real FastAPI router from
``pke.adapters.browser_ext_endpoint`` against a temporary ``App`` so
the normal ingestion pipeline (normalize -> dedup -> persist) is
exercised end to end.

We use ``httpx.AsyncClient`` with ``ASGITransport`` so the route handler
runs on the test's asyncio loop. The default ``TestClient`` would
dispatch on a worker thread, which conflicts with sqlite3's
``check_same_thread`` connection setting.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from pke.adapters.browser_ext_endpoint import router as evidence_router


@pytest.fixture()
def http_app(app):
    """FastAPI app wired to the temp evidence store."""
    web = FastAPI()
    web.include_router(evidence_router(lambda: app))
    return web


async def _post(http_app, payload):
    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/evidence", json=payload)


async def test_content_script_payload_lands_in_evidence_store(app, http_app):
    """A content-script payload lands in the evidence store.

    A ``browser_ext_chatgpt`` payload from the content script normalizes,
    persists, and is queryable from the evidence store with the correct
    source, conversation_id, and turn content.
    """
    t0_ms = int(time.time() * 1000)
    payload = {
        "source": "browser_ext_chatgpt",
        "conversation_id": "01940c0c-aaaa-bbbb-cccc-deadbeefdead",
        "turn_index": 0,
        "user_message_id": "user-msg-1",
        "assistant_message_id": "asst-msg-1",
        "user_text": "How do FastAPI dependency overrides work in tests?",
        "assistant_text": (
            "FastAPI exposes app.dependency_overrides as a dict. "
            "Assign override functions to the original dependency keys."
        ),
        "t0": t0_ms,
        "url": "https://chatgpt.com/c/01940c0c-aaaa-bbbb-cccc-deadbeefdead",
    }

    resp = await _post(http_app, payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "new"
    assert body["id"]

    # The event is queryable through the normal SQLite store with the
    # extension's source name. We don't go through any test-only path —
    # this is the same query the review pipeline runs.
    rows = list(
        app.sqlite.conn.execute(
            "SELECT id, source, source_session_id, role, content "
            "FROM evidence_events WHERE source = ?",
            ("browser_ext_chatgpt",),
        )
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "user"
    assert "FastAPI dependency overrides" in row["content"]
    assert "dependency_overrides" in row["content"]
    # The conversation_id is namespaced by app on insert so cross-app
    # collisions stay separate.
    assert row["source_session_id"].startswith("chatgpt_web_")


async def test_repeat_post_is_deduplicated(app, http_app):
    """Posting the same content twice yields dup_exact on the second call."""
    payload = {
        "source": "browser_ext_chatgpt",
        "conversation_id": "01940c0c-cafe-cafe-cafe-cafecafecafe",
        "turn_index": 0,
        "user_message_id": "u1",
        "assistant_message_id": "a1",
        "user_text": "What is mypy strict mode?",
        "assistant_text": "It turns on the strictest type-checking flags.",
        "t0": int(time.time() * 1000),
        "url": "https://chatgpt.com/c/01940c0c-cafe-cafe-cafe-cafecafecafe",
    }
    first = (await _post(http_app, payload)).json()
    second = (await _post(http_app, payload)).json()
    assert first["status"] == "new"
    assert second["status"] == "dup_exact"
    assert second["id"] == first["id"]


async def test_legacy_reqbody_payload_still_works(app, http_app):
    """Legacy fetch-interception payloads still ingest.

    The older ``reqBody``/``body`` payload shape (used by the previous
    network-interception content script) keeps working under the
    generic ``browser_ext`` source.
    """
    payload = {
        "url": "https://chatgpt.com/backend-api/conversation",
        "reqBody": '{"messages":[{"content":"legacy capture"}]}',
        "body": "legacy assistant reply",
        "kind": "stream",
        "status": 200,
        "t0": int(time.time() * 1000),
    }
    resp = await _post(http_app, payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "new"
    rows = list(
        app.sqlite.conn.execute(
            "SELECT source FROM evidence_events WHERE source = ?",
            ("browser_ext",),
        )
    )
    assert len(rows) == 1
