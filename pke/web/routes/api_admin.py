"""Admin / health API routes.

Exposes the ARCH-2 drift metrics so the admin dashboard (and any
external dashboard / alerting) can render the green-yellow-red bands
without rewriting the band logic on the front end.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from pke.quality.llm_log import daily_cost_by_provider
from pke.quality.metrics import (
    METRIC_ARI_WEEK,
    METRIC_CENTROID_COUNT,
    METRIC_LLM_COST_30D,
    latest_metric,
    metric_series,
)

_TRACKED_METRICS = (METRIC_CENTROID_COUNT, METRIC_ARI_WEEK, METRIC_LLM_COST_30D)

# Default history windows per metric for the admin dashboard charts.
# centroid_count is sampled daily, ari_week weekly, llm_cost_30d daily.
# Callers can override these via query params (see ``/drift/history``).
CENTROID_HISTORY_POINTS = 30
ARI_HISTORY_POINTS = 12
COST_HISTORY_DAYS = 30

# Inclusive bounds enforced by the ``/drift/history`` endpoint. Days are
# capped at one year; weeks at one year of weekly samples. The lower
# bound is 1 so a request always returns at least one point when data
# exists.
MIN_HISTORY_DAYS = 1
MAX_HISTORY_DAYS = 365
MIN_HISTORY_WEEKS = 1
MAX_HISTORY_WEEKS = 52


def router(store_getter: Any) -> APIRouter:
    """Build admin / health API routes."""
    api = APIRouter(prefix="/api/v1/admin")

    @api.get("/drift")
    async def drift() -> dict[str, Any]:
        app = store_getter()
        result: dict[str, Any] = {}
        for name in _TRACKED_METRICS:
            snap = latest_metric(app.sqlite, metric_name=name)
            if snap is None:
                result[name] = {"value": None, "band": "no_data", "recorded_at": None}
                continue
            result[name] = {
                "value": snap.value,
                "band": snap.band.value,
                "recorded_at": snap.recorded_at,
                "payload": snap.payload,
            }
        return result

    @api.get("/drift/history")
    async def drift_history(
        centroid_days: int = Query(
            CENTROID_HISTORY_POINTS,
            ge=MIN_HISTORY_DAYS,
            le=MAX_HISTORY_DAYS,
            description="Centroid sparkline window in daily samples.",
        ),
        ari_weeks: int = Query(
            ARI_HISTORY_POINTS,
            ge=MIN_HISTORY_WEEKS,
            le=MAX_HISTORY_WEEKS,
            description="ARI sparkline window in weekly samples.",
        ),
        cost_days: int = Query(
            COST_HISTORY_DAYS,
            ge=MIN_HISTORY_DAYS,
            le=MAX_HISTORY_DAYS,
            description="LLM cost bar window in daily buckets.",
        ),
    ) -> dict[str, Any]:
        """Return time-series for each tracked metric.

        ``centroid_count`` and ``ari_week`` are returned as ordered
        lists of ``{value, band, recorded_at}`` entries (chronological,
        oldest-first). ``llm_cost_30d`` is returned as a per-day
        per-provider breakdown so the admin dashboard can stack
        anthropic / openai / local bars.

        Window sizes are controlled by ``centroid_days``, ``ari_weeks``,
        and ``cost_days``; out-of-bounds values fail with HTTP 422.
        """
        app = store_getter()
        centroid_series = metric_series(
            app.sqlite,
            metric_name=METRIC_CENTROID_COUNT,
            limit=centroid_days,
        )
        ari_series = metric_series(
            app.sqlite, metric_name=METRIC_ARI_WEEK, limit=ari_weeks
        )
        cost_daily = daily_cost_by_provider(app.sqlite, days=cost_days)
        return {
            "centroid_count": [
                {
                    "value": s.value,
                    "band": s.band.value,
                    "recorded_at": s.recorded_at,
                }
                for s in centroid_series
            ],
            "ari_week": [
                {
                    "value": s.value,
                    "band": s.band.value,
                    "recorded_at": s.recorded_at,
                }
                for s in ari_series
            ],
            "llm_cost_30d": cost_daily,
        }

    return api
