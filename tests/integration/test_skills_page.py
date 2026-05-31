"""Integration tests for the Skills web page and the review-batch contract.

These tests stand up the full FastAPI app against a temporary database
and drive it through ``httpx.AsyncClient``. They cover:

- ``GET /skills`` returns 200 and renders the table structure with one
  row per active skill ordered by slippage (highest first).
- ``GET /partials/skills-table`` returns just the ``<tbody>`` fragment
  for htmx sort swaps, honoring ``?sort=``.
- ``POST /api/v1/review/start?limit=N`` builds a session with up to N
  items and rejects out-of-range values with 422.
- ``GET /dashboard`` renders the queue preview pulled from the same
  selector the review endpoint uses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from httpx import ASGITransport

from pke.app import App
from pke.config.settings import Settings
from pke.testing import MockLLMClient
from pke.web.main import create_app


def _extract_app_state(web: object) -> App:
    """Pull the App instance out of the FastAPI app's route closures.

    ``create_app`` stores the App via a closure over ``store_getter``;
    every router uses the same closure, so we walk the registered routes
    until we find one whose closure includes ``store_getter`` and call it.
    """
    for route in web.router.routes:  # type: ignore[attr-defined]
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        closure = getattr(endpoint, "__closure__", None) or ()
        for cell in closure:
            try:
                target = cell.cell_contents
            except ValueError:
                continue
            if callable(target) and getattr(target, "__name__", "") == "store_getter":
                state = target()
                if isinstance(state, App):
                    return state
            if isinstance(target, App):
                return target
    raise RuntimeError("could not find App inside FastAPI app")


@pytest.fixture()
async def web_client() -> AsyncIterator[tuple[httpx.AsyncClient, App]]:
    """Yield (async http client, App) bound to a fresh temp database."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(
            data_dir=root / "data",
            config_path=root / "config.toml",
            intervention_per_source={},
        )
        web = create_app(settings=settings)
        app_state = _extract_app_state(web)
        # Wire up a deterministic LLM so review item generation works.
        app_state.llm_client = MockLLMClient()
        transport = ASGITransport(app=web)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            try:
                yield client, app_state
            finally:
                app_state.close()


def _insert_skill(
    app: App,
    *,
    skill_id: str,
    name: str,
    unaided_retrievability: float,
    unaided_lapses: int = 0,
    outsource_count_7d: int = 0,
    days_since_review: int | None = 1,
) -> None:
    """Insert one active skill plus a mastery row for it."""
    now = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    last_review: str | None = None
    if days_since_review is not None:
        last_review = (
            (datetime.now(tz=UTC) - timedelta(days=days_since_review))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding, first_seen_at, last_seen_at,
          created_at, updated_at
        )
        VALUES (?, ?, '', zeroblob(3072), ?, ?, ?, ?)
        """,
        (skill_id, name, now, now, now, now),
    )
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_mastery_state(
          skill_id, unaided_retrievability, unaided_reps, unaided_lapses,
          unaided_last_review_at, outsource_count_7d, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_id,
            unaided_retrievability,
            0,
            unaided_lapses,
            last_review,
            outsource_count_7d,
            now,
        ),
    )
    app.sqlite.conn.commit()


async def test_skills_page_returns_table_sorted_by_slippage(web_client):
    """The /skills page renders skills with the most-slipping one first."""
    client, app = web_client
    _insert_skill(
        app,
        skill_id="weak-skill",
        name="async context managers",
        unaided_retrievability=0.15,
        outsource_count_7d=6,
    )
    _insert_skill(
        app,
        skill_id="strong-skill",
        name="python dataclasses",
        unaided_retrievability=0.92,
        outsource_count_7d=0,
    )
    response = await client.get("/skills")
    assert response.status_code == 200
    body = response.text
    assert "Skills (2)" in body
    assert "async context managers" in body
    assert "python dataclasses" in body
    # The slippage-sorted order places the weak skill first.
    weak_pos = body.index("async context managers")
    strong_pos = body.index("python dataclasses")
    assert weak_pos < strong_pos
    # Expected table structure: header columns + the sort form.
    for header in ("Unaided", "Functional", "Last reviewed", "Slippage"):
        assert header in body
    assert 'name="sort"' in body
    # Review-now button targets the per-skill review endpoint.
    assert "review-now" in body
    assert 'hx-post="/api/v1/review/start?limit=1"' in body


async def test_skills_table_partial_honors_sort(web_client):
    """The htmx fragment swaps rows for the requested sort key."""
    client, app = web_client
    _insert_skill(
        app,
        skill_id="alpha",
        name="alpha skill",
        unaided_retrievability=0.4,
        outsource_count_7d=0,
    )
    _insert_skill(
        app,
        skill_id="beta",
        name="beta skill",
        unaided_retrievability=0.4,
        outsource_count_7d=0,
    )
    response = await client.get("/partials/skills-table?sort=name")
    assert response.status_code == 200
    body = response.text
    # Just the tbody fragment, not a full page.
    assert "<tbody" in body
    assert "<html" not in body
    # Name sort: alpha before beta.
    assert body.index("alpha skill") < body.index("beta skill")


async def test_dashboard_renders_queue_preview(web_client):
    """The dashboard shows queue count and minutes-estimate from the selector."""
    client, app = web_client
    for idx in range(3):
        _insert_skill(
            app,
            skill_id=f"skill-{idx}",
            name=f"skill {idx}",
            unaided_retrievability=0.3,
            outsource_count_7d=4,
        )
    response = await client.get("/dashboard")
    assert response.status_code == 200
    body = response.text
    # 3 active skills → 3 queued items → ~6 minutes at 2 minutes/item.
    assert "<strong>3</strong> items queued" in body
    assert "<strong>6</strong> minutes" in body
    # Session-length dropdown is rendered.
    assert 'name="limit"' in body
    for option in ("5 items", "10 items", "20 items", "50 items"):
        assert option in body


async def test_review_start_limit_via_query(web_client):
    """``POST /api/v1/review/start?limit=N`` returns N items (capped by candidates)."""
    client, app = web_client
    for idx in range(25):
        _insert_skill(
            app,
            skill_id=f"skill-{idx:02d}",
            name=f"skill {idx}",
            unaided_retrievability=0.4,
            outsource_count_7d=2,
        )
    response = await client.post("/api/v1/review/start?limit=20", json={})
    assert response.status_code == 200, response.text
    data = response.json()
    assert "session_id" in data
    assert len(data["items"]) == 20

    # The body-only form still works for back-compat.
    response_body = await client.post("/api/v1/review/start", json={"limit": 3})
    assert response_body.status_code == 200, response_body.text
    assert len(response_body.json()["items"]) == 3


async def test_review_start_rejects_out_of_range_limit(web_client):
    """Limit must be in [1, 50]; anything outside that range is 422."""
    client, _app = web_client
    # Above the cap.
    too_big = await client.post("/api/v1/review/start?limit=999", json={})
    assert too_big.status_code == 422
    # Zero is rejected by ge=1.
    zero = await client.post("/api/v1/review/start?limit=0", json={})
    assert zero.status_code == 422
    # Body-side validation matches.
    body_bad = await client.post("/api/v1/review/start", json={"limit": 999})
    assert body_bad.status_code == 422


async def test_review_start_default_limit_is_five(web_client):
    """When neither query nor body provides a limit, default is 5."""
    client, app = web_client
    for idx in range(10):
        _insert_skill(
            app,
            skill_id=f"d-{idx}",
            name=f"default skill {idx}",
            unaided_retrievability=0.4,
        )
    response = await client.post("/api/v1/review/start", json={})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 5
