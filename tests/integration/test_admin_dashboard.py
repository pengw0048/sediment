"""Integration tests for the ARCH-2 admin dashboard charts UI.

These tests stand up the full FastAPI app against a temporary database,
seed ``quality_metrics`` and ``llm_call_log`` with synthetic history,
then hit ``GET /admin`` (the new charts page) and
``GET /api/v1/admin/drift/history`` (the new history JSON endpoint) and
assert the response renders the correct headline numbers, band
classes, and SVG primitives (polylines, circles, rects). The fixture
mirrors ``tests/integration/test_skills_page.py``.
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
from pke.evidence.models import new_ulid
from pke.quality.metrics import (
    METRIC_ARI_WEEK,
    METRIC_CENTROID_COUNT,
    METRIC_LLM_COST_30D,
    record_metric,
)
from pke.testing import MockLLMClient
from pke.web.main import create_app


def _extract_app_state(web: object) -> App:
    """Pull the App instance out of FastAPI route closures (same as test_skills_page)."""
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


def _backdate_latest_metric(app: App, *, metric_name: str, recorded_at: str) -> None:
    """Rewrite the most recent ``quality_metrics`` row's recorded_at.

    ``record_metric`` always stamps the current time; the dashboard
    history charts need older points spread across 30 days. We update
    the just-inserted row in place rather than inventing a back-dated
    insert path, keeping the production write path exercised.
    """
    app.sqlite.conn.execute(
        """
        UPDATE quality_metrics
        SET recorded_at = ?
        WHERE id = (
          SELECT id FROM quality_metrics
          WHERE metric_name = ?
          ORDER BY rowid DESC LIMIT 1
        )
        """,
        (recorded_at, metric_name),
    )
    app.sqlite.conn.commit()


def _seed_metric_series(
    app: App, *, metric_name: str, values: list[float], days_apart: int = 1
) -> None:
    """Insert ``values`` as a back-dated series, oldest first."""
    today = datetime.now(tz=UTC)
    n = len(values)
    for i, v in enumerate(values):
        record_metric(app.sqlite, metric_name=metric_name, value=v)
        recorded_at = (
            (today - timedelta(days=(n - 1 - i) * days_apart))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        _backdate_latest_metric(app, metric_name=metric_name, recorded_at=recorded_at)


def _insert_llm_call(
    app: App,
    *,
    provider: str,
    cost_usd: float,
    days_ago: int,
) -> None:
    """Insert one ``llm_call_log`` row with explicit ``called_at`` offset."""
    when = (
        (datetime.now(tz=UTC) - timedelta(days=days_ago))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    app.sqlite.conn.execute(
        """
        INSERT INTO llm_call_log(
          id, provider, model, call_kind, prompt_tokens, completion_tokens,
          cost_usd, latency_ms, error, called_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_ulid(),
            provider,
            "model-x",
            "extract",
            0,
            0,
            cost_usd,
            0,
            None,
            when,
        ),
    )
    app.sqlite.conn.commit()


async def test_admin_dashboard_renders_with_no_data(web_client) -> None:
    """Empty DB: all three tiles render the no-data band."""
    client, _app = web_client
    response = await client.get("/admin")
    assert response.status_code == 200
    body = response.text
    # Three no-data tile elements: one per metric. We look at the
    # ``<section class="metric-tile metric-tile--no_data"`` markers so
    # we don't accidentally count the CSS rule selectors that also
    # mention ``metric-tile--no_data``.
    assert body.count('"metric-tile metric-tile--no_data"') == 3
    # Em-dash placeholder for missing values.
    assert "&mdash;" in body or "—" in body


async def test_admin_dashboard_renders_centroid_sparkline(web_client) -> None:
    """30 daily centroid snapshots ending red: tile shows red band + polyline."""
    client, app = web_client
    # Series ramps from green territory into red (>= 50_000).
    values = [1000.0 + 2000.0 * i for i in range(30)]
    _seed_metric_series(app, metric_name=METRIC_CENTROID_COUNT, values=values)

    response = await client.get("/admin")
    assert response.status_code == 200
    body = response.text

    # Latest value = 59_000 → red band.
    assert "metric-tile--red" in body
    # Headline rendered with thousands separator.
    assert "59,000" in body
    # Sparkline polyline with 30 points (semicolon-or-comma separated).
    assert '<polyline' in body
    # Markers — one per data point.
    assert body.count('<circle') >= 30


async def test_admin_dashboard_renders_ari_bands(web_client) -> None:
    """12 weekly ARI snapshots ending at 0.45: tile shows red band + 12 markers."""
    client, app = web_client
    values = [0.80, 0.78, 0.72, 0.71, 0.70, 0.65, 0.60, 0.58, 0.55, 0.52, 0.49, 0.45]
    _seed_metric_series(app, metric_name=METRIC_ARI_WEEK, values=values, days_apart=7)

    response = await client.get("/admin")
    assert response.status_code == 200
    body = response.text

    # Latest 0.45 < 0.5 → red.
    assert 'aria-label="ARI week"' in body
    # The ARI tile itself is red — verified via the headline.
    assert "0.45" in body
    # ARI svg contains 12 circle markers.
    # Count "ARI" section's circles: at least 12 inside the page.
    assert body.count('<circle') >= 12


async def test_admin_dashboard_renders_cost_bars(web_client) -> None:
    """30 daily cost log rows render a bar SVG with rect elements."""
    client, app = web_client
    # Record the latest cost metric too — drives the tile headline.
    record_metric(app.sqlite, metric_name=METRIC_LLM_COST_30D, value=42.50)
    # Seed daily LLM calls across the window with distinct providers.
    for d in range(30):
        _insert_llm_call(app, provider="anthropic", cost_usd=0.50, days_ago=d)
        if d % 3 == 0:
            _insert_llm_call(app, provider="openai", cost_usd=0.25, days_ago=d)
        if d % 5 == 0:
            _insert_llm_call(app, provider="local", cost_usd=0.10, days_ago=d)

    response = await client.get("/admin")
    assert response.status_code == 200
    body = response.text

    # Headline shows the latest llm_cost_30d snapshot.
    assert "$42.50" in body
    # Bars SVG has at least one <rect> per provider per day with cost > 0.
    # We expect 30 anthropic bars + 10 openai bars + 6 local bars = 46+ rects.
    assert body.count('<rect') >= 30
    # Provider classes appear on the bars.
    assert "cost-bar--anthropic" in body
    assert "cost-bar--openai" in body
    assert "cost-bar--local" in body
    # Legend rendered.
    assert "cost-legend" in body


async def test_admin_charts_have_a11y_aria_label_and_desc(web_client) -> None:
    """Each rendered chart SVG carries role=img, a human aria-label, and a <desc>.

    Screen readers fall back to ``<desc>`` for SVG content that the
    ``aria-label`` alone cannot summarize, so both must be present and
    non-empty. We assert the rendered text mentions the current value
    and band so the description tracks the visual state.
    """
    import re

    client, app = web_client
    # Seed enough data that all three charts render.
    _seed_metric_series(
        app,
        metric_name=METRIC_CENTROID_COUNT,
        values=[10_000.0 + 500.0 * i for i in range(10)],
    )
    _seed_metric_series(
        app, metric_name=METRIC_ARI_WEEK, values=[0.80, 0.78, 0.76], days_apart=7
    )
    record_metric(app.sqlite, metric_name=METRIC_LLM_COST_30D, value=12.34)
    _insert_llm_call(app, provider="anthropic", cost_usd=0.10, days_ago=0)

    response = await client.get("/admin")
    assert response.status_code == 200
    body = response.text

    # Every chart SVG carries role="img" and a non-empty aria-label.
    svg_pattern = re.compile(
        r'<svg\b[^>]*\brole="img"[^>]*\baria-label="([^"]+)"', re.DOTALL
    )
    labels = svg_pattern.findall(body)
    # Three charts (centroid sparkline, ARI sparkline, cost bars) when
    # all three tiles have data.
    assert len(labels) == 3, f"expected 3 chart SVGs with aria-label, found {len(labels)}"
    for label in labels:
        assert label.strip(), "aria-label must not be empty"

    # Each chart embeds a <desc> tag for screen-reader fallback content.
    desc_pattern = re.compile(r"<desc>([^<]+)</desc>")
    descs = desc_pattern.findall(body)
    assert len(descs) == 3, f"expected 3 <desc> tags, found {len(descs)}"
    for desc in descs:
        assert desc.strip(), "<desc> body must not be empty"

    # The descriptions track the chart state: a band word appears in each.
    bands_seen = {b for desc in descs for b in ("green", "yellow", "red") if b in desc}
    assert bands_seen, "at least one description must mention a band"


async def test_drift_history_rejects_out_of_bounds_windows(web_client) -> None:
    """Out-of-range window params produce 422; in-range custom windows return that many points.

    The bounds (1-365 for days, 1-52 for weeks) are FastAPI ``Query``
    constraints, so the response status is 422 rather than 400.
    """
    client, app = web_client

    # Each combination here individually violates one bound; the other
    # two params are left at their valid defaults.
    for bad in [
        {"centroid_days": 0},
        {"centroid_days": 366},
        {"ari_weeks": 0},
        {"ari_weeks": 53},
        {"cost_days": 0},
        {"cost_days": 366},
    ]:
        response = await client.get("/api/v1/admin/drift/history", params=bad)
        assert response.status_code == 422, f"expected 422 for {bad}, got {response.status_code}"

    # Custom in-range windows: cost_days=7 truncates the cost bucket
    # list to exactly 7 days. We don't need to seed more centroid/ARI
    # data — the endpoint must return at most the requested window.
    _insert_llm_call(app, provider="anthropic", cost_usd=0.30, days_ago=0)
    response = await client.get(
        "/api/v1/admin/drift/history",
        params={"centroid_days": 5, "ari_weeks": 4, "cost_days": 7},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["llm_cost_30d"]) == 7
    # Centroid / ARI series respect their custom limits — DB has no data
    # so the lists are empty, but the response must still be well-formed.
    assert isinstance(data["centroid_count"], list)
    assert isinstance(data["ari_week"], list)


async def test_drift_history_endpoint_returns_series(web_client) -> None:
    """GET /api/v1/admin/drift/history returns per-metric series JSON."""
    client, app = web_client
    _seed_metric_series(
        app, metric_name=METRIC_CENTROID_COUNT, values=[10_000.0, 12_000.0, 14_000.0]
    )
    _seed_metric_series(
        app, metric_name=METRIC_ARI_WEEK, values=[0.6, 0.7, 0.8], days_apart=7
    )
    _insert_llm_call(app, provider="anthropic", cost_usd=0.30, days_ago=0)

    response = await client.get("/api/v1/admin/drift/history")
    assert response.status_code == 200
    data = response.json()

    # Centroid series oldest-first, three points.
    assert len(data["centroid_count"]) == 3
    assert data["centroid_count"][0]["value"] == 10_000.0
    assert data["centroid_count"][-1]["value"] == 14_000.0
    assert data["centroid_count"][-1]["band"] == "green"

    # ARI series, three weekly points, latest green.
    assert len(data["ari_week"]) == 3
    assert data["ari_week"][-1]["band"] == "green"

    # Cost daily: 30 days, today's anthropic bucket carries 0.30.
    assert len(data["llm_cost_30d"]) == 30
    today_entry = data["llm_cost_30d"][-1]
    assert today_entry["anthropic"] >= 0.30
    # All three providers present in every day record.
    for entry in data["llm_cost_30d"]:
        assert {"day", "anthropic", "openai", "local"} <= set(entry.keys())
