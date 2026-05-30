"""Admin / health API routes.

Exposes the ARCH-2 drift metrics so the admin dashboard (and any
external dashboard / alerting) can render the green-yellow-red bands
without rewriting the band logic on the front end.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pke.quality.metrics import (
    METRIC_ARI_WEEK,
    METRIC_CENTROID_COUNT,
    METRIC_LLM_COST_30D,
    latest_metric,
)

_TRACKED_METRICS = (METRIC_CENTROID_COUNT, METRIC_ARI_WEEK, METRIC_LLM_COST_30D)


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

    return api
