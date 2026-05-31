"""Integration tests for the per-skill review-now endpoint.

The Skills page has a "Review now" button that posts to
``/api/v1/review/start`` with ``skill_id`` set, asking the server to
build a one-item review session for that specific skill, bypassing the
weighted-sum scheduler. These tests pin that contract:

- A skill targeted by ``skill_id`` is the only one in the resulting
  session, even when other (higher-priority) skills exist.
- A missing or inactive skill returns 404 rather than silently falling
  back to the scheduler.
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
    """Pull the App instance out of the FastAPI app's route closures."""
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
        VALUES (?, ?, 0, 0, ?, ?, ?)
        """,
        (
            skill_id,
            unaided_retrievability,
            last_review,
            outsource_count_7d,
            now,
        ),
    )
    app.sqlite.conn.commit()


async def test_review_start_with_skill_id_targets_that_skill(web_client):
    """``skill_id`` (body) builds a 1-item session targeting that skill only.

    Seed two skills with very different urgencies — the scheduler would
    pick ``urgent`` first — and ask for the calm one by id. The session
    must contain exactly one item against ``calm``, proving the
    scheduler was bypassed.
    """
    client, app = web_client
    _insert_skill(
        app,
        skill_id="urgent",
        name="urgent skill",
        unaided_retrievability=0.05,
        outsource_count_7d=9,
    )
    _insert_skill(
        app,
        skill_id="calm",
        name="calm skill",
        unaided_retrievability=0.95,
        outsource_count_7d=0,
    )
    response = await client.post(
        "/api/v1/review/start?limit=1",
        json={"skill_id": "calm", "client": "web"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    session_id = data["session_id"]
    item_ids = data["items"]
    assert len(item_ids) == 1

    row = app.sqlite.conn.execute(
        "SELECT skill_id FROM review_items WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    assert [str(r["skill_id"]) for r in row] == ["calm"]


async def test_review_start_skill_id_via_query_string(web_client):
    """``?skill_id=...`` works as well as the body form."""
    client, app = web_client
    _insert_skill(
        app,
        skill_id="alpha",
        name="alpha skill",
        unaided_retrievability=0.5,
    )
    _insert_skill(
        app,
        skill_id="beta",
        name="beta skill",
        unaided_retrievability=0.5,
    )
    response = await client.post(
        "/api/v1/review/start?skill_id=beta&limit=1",
        json={},
    )
    assert response.status_code == 200, response.text
    item_ids = response.json()["items"]
    assert len(item_ids) == 1
    row = app.sqlite.conn.execute(
        "SELECT skill_id FROM review_items WHERE id = ?",
        (item_ids[0],),
    ).fetchone()
    assert str(row["skill_id"]) == "beta"


async def test_review_start_unknown_skill_id_returns_404(web_client):
    """A missing/inactive ``skill_id`` is a 404, not a silent fallback."""
    client, app = web_client
    _insert_skill(
        app,
        skill_id="real",
        name="real skill",
        unaided_retrievability=0.4,
    )
    missing = await client.post(
        "/api/v1/review/start?limit=1",
        json={"skill_id": "does-not-exist"},
    )
    assert missing.status_code == 404
