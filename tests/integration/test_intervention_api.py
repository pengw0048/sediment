"""Integration test for the pre-Send intervention API.

The MV3 browser extension hits ``/api/v1/intervention/check`` before
forwarding the user's Send click, and ``/api/v1/intervention/outcome``
afterwards. This test pins the exact request and response shape the
extension relies on, so a server-side refactor can't silently change
the contract.

We drive the real FastAPI router (``pke.web.routes.api_intervention``)
against the temp ``App`` fixture so the gate chain, sqlite log, and
toast-payload shaping all run end to end.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from pke.intervention.state import InterventionStateStore
from pke.intervention.strength import StrengthLevel
from pke.web.routes import api_intervention


@pytest.fixture()
def http_app(app):
    """FastAPI app wired to the temp Sediment app's intervention router."""
    web = FastAPI()
    web.include_router(api_intervention.router(lambda: app))
    return web


async def _post(http_app, path, payload):
    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=payload)


def _force_active(app, source: str) -> None:
    """Pin ``source`` to ACTIVE so /check returns intervene=True deterministically.

    With the default GENTLE strength the decider waits 30 calls before
    firing — too noisy for a single-request integration test. ACTIVE
    bypasses the gentle cooldown entirely.
    """
    store = InterventionStateStore(sqlite=app.sqlite)
    store.set_override(source=source, level=StrengthLevel.ACTIVE)


async def test_check_with_extension_payload_returns_intervene_payload(app, http_app):
    """The pre-Send card payload the browser sends comes back with intervene=True.

    Verifies the shape the extension assumes:

    - ``source`` and ``skill_label="unknown"`` are accepted with no
      server-side skill resolution.
    - Mastery defaults to 0.5 server-side (inside the gate band).
    - The response carries ``intervene: True`` with a ``payload`` dict
      that contains a ``question`` string for the card heading.
    """
    _force_active(app, "browser_ext_chatgpt")

    resp = await _post(
        http_app,
        "/api/v1/intervention/check",
        {
            "source": "browser_ext_chatgpt",
            "skill_label": "unknown",
            "context_summary": "How do I configure FastAPI dependency overrides?",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["intervene"] is True
    payload = body["payload"]
    assert isinstance(payload["question"], str)
    assert payload["question"]


async def test_check_returns_no_intervene_when_deadline_mode_engaged(app, http_app):
    """Deadline mode suppresses interventions for every source.

    The card must render only when the server says so; the extension
    interprets ``{intervene: False}`` (and any non-2xx / timeout) as
    "let the user's Send fire untouched".
    """
    store = InterventionStateStore(sqlite=app.sqlite)
    store.set_deadline_mode(hours=1.0)

    resp = await _post(
        http_app,
        "/api/v1/intervention/check",
        {
            "source": "browser_ext_chatgpt",
            "skill_label": "unknown",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"intervene": False}


async def test_outcome_dismissed_and_engaged_round_trip(app, http_app):
    """/check shows a row, then /outcome flips it to dismissed/engaged.

    Walks the full extension flow:

    1. /check fires (extension would render the card).
    2. /outcome with ``dismissed`` (Skip) lands a row in intervention_log.
    3. A second /check + /outcome with ``engaged`` carries the user's
       typed response through into the log.
    """
    _force_active(app, "browser_ext_chatgpt")

    # First check + Skip path.
    first = await _post(
        http_app,
        "/api/v1/intervention/check",
        {"source": "browser_ext_chatgpt", "skill_label": "unknown"},
    )
    assert first.json()["intervene"] is True

    skip = await _post(
        http_app,
        "/api/v1/intervention/outcome",
        {
            "source": "browser_ext_chatgpt",
            "outcome": "dismissed",
            "user_response": None,
        },
    )
    assert skip.status_code == 200
    assert skip.json()["log_id"]

    # Second check + Answer path.
    second = await _post(
        http_app,
        "/api/v1/intervention/check",
        {"source": "browser_ext_chatgpt", "skill_label": "unknown"},
    )
    assert second.json()["intervene"] is True

    engaged = await _post(
        http_app,
        "/api/v1/intervention/outcome",
        {
            "source": "browser_ext_chatgpt",
            "outcome": "engaged",
            "user_response": "Check the kubeconfig context before describing pods.",
        },
    )
    assert engaged.status_code == 200
    engaged_id = engaged.json()["log_id"]
    assert engaged_id

    # The engaged outcome's user_response is persisted in intervention_log
    # so downstream review pipelines can score the answer.
    row = app.sqlite.conn.execute(
        "SELECT outcome, source, user_response FROM intervention_log "
        "WHERE log_id = ?",
        (engaged_id,),
    ).fetchone()
    assert row is not None
    assert row["outcome"] == "engaged"
    assert row["source"] == "browser_ext_chatgpt"
    assert row["user_response"] == "Check the kubeconfig context before describing pods."


async def test_outcome_rejects_unknown_outcome_kind(app, http_app):
    """The server hard-rejects outcomes outside the small enum.

    The extension only ever posts ``dismissed`` or ``engaged``, but this
    pins the 422 contract so a typo on the client side surfaces loudly
    instead of being silently logged.
    """
    resp = await _post(
        http_app,
        "/api/v1/intervention/outcome",
        {"source": "browser_ext_chatgpt", "outcome": "ignored"},
    )
    assert resp.status_code == 422
