"""Admin / health API routes.

Exposes the ARCH-2 drift metrics so the admin dashboard (and any
external dashboard / alerting) can render the green-yellow-red bands
without rewriting the band logic on the front end.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pke.quality.llm_log import daily_cost_by_provider
from pke.quality.metrics import (
    METRIC_ARI_WEEK,
    METRIC_CENTROID_COUNT,
    METRIC_LLM_COST_30D,
    latest_metric,
    metric_series,
)

_TRACKED_METRICS = (METRIC_CENTROID_COUNT, METRIC_ARI_WEEK, METRIC_LLM_COST_30D)

# History windows per metric for the admin dashboard charts.
# centroid_count is sampled daily, ari_week weekly, llm_cost_30d daily.
CENTROID_HISTORY_POINTS = 30
ARI_HISTORY_POINTS = 12
COST_HISTORY_DAYS = 30


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
    async def drift_history() -> dict[str, Any]:
        """Return time-series for each tracked metric.

        ``centroid_count`` and ``ari_week`` are returned as ordered
        lists of ``{value, band, recorded_at}`` entries (chronological,
        oldest-first). ``llm_cost_30d`` is returned as a per-day
        per-provider breakdown so the admin dashboard can stack
        anthropic / openai / local bars.
        """
        app = store_getter()
        centroid_series = metric_series(
            app.sqlite,
            metric_name=METRIC_CENTROID_COUNT,
            limit=CENTROID_HISTORY_POINTS,
        )
        ari_series = metric_series(
            app.sqlite, metric_name=METRIC_ARI_WEEK, limit=ARI_HISTORY_POINTS
        )
        cost_daily = daily_cost_by_provider(app.sqlite, days=COST_HISTORY_DAYS)
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
