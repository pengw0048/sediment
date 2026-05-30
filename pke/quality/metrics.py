"""ARCH-2 quality_metrics read/write helpers + banding rules."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc, new_ulid

METRIC_CENTROID_COUNT = "centroid_count"
METRIC_ARI_WEEK = "ari_week"
METRIC_LLM_COST_30D = "llm_cost_30d"


class Band(StrEnum):
    """Green/yellow/red banding from the ARCH-2 spec."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    INFO = "info"  # for metrics that are logged but have no thresholds


@dataclass(frozen=True, kw_only=True, slots=True)
class MetricSnapshot:
    """One row of quality_metrics, decoded for code use."""

    metric_name: str
    value: float
    band: Band
    recorded_at: str
    payload: dict[str, object]


def record_metric(
    sqlite: SQLiteStore,
    *,
    metric_name: str,
    value: float,
    payload: dict[str, object] | None = None,
) -> str:
    """Persist a new sample to ``quality_metrics``. Returns the new row id."""
    metric_id = new_ulid()
    sqlite.conn.execute(
        """
        INSERT INTO quality_metrics(id, metric_name, value, payload_json, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            metric_id,
            metric_name,
            float(value),
            json.dumps(payload or {}),
            iso_utc(),
        ),
    )
    sqlite.conn.commit()
    return metric_id


def latest_metric(sqlite: SQLiteStore, *, metric_name: str) -> MetricSnapshot | None:
    """Return the most recent snapshot for ``metric_name`` (or ``None``).

    Ties on ``recorded_at`` are broken by SQLite's implicit ``rowid``,
    which is monotonic per-table — without that tie-breaker two rows
    inserted in the same millisecond would return in arbitrary order.
    The ULID id has a random tail and is *not* a reliable tiebreaker.
    """
    row = sqlite.conn.execute(
        """
        SELECT metric_name, value, payload_json, recorded_at
        FROM quality_metrics
        WHERE metric_name = ?
        ORDER BY recorded_at DESC, rowid DESC
        LIMIT 1
        """,
        (metric_name,),
    ).fetchone()
    if row is None:
        return None
    payload_raw = row["payload_json"] or "{}"
    try:
        payload = json.loads(str(payload_raw))
    except json.JSONDecodeError:
        payload = {}
    return MetricSnapshot(
        metric_name=str(row["metric_name"]),
        value=float(row["value"] or 0.0),
        band=band_for(str(row["metric_name"]), float(row["value"] or 0.0)),
        recorded_at=str(row["recorded_at"]),
        payload=payload,
    )


def band_for(metric_name: str, value: float) -> Band:  # noqa: PLR0911
    """Map ``(metric_name, value)`` to the green/yellow/red band per ARCH-2.

    Unknown metrics return :attr:`Band.INFO` rather than raising so a new
    metric name can be added without crashing readers that haven't been
    updated.
    """
    if metric_name == METRIC_CENTROID_COUNT:
        if value < 25_000:
            return Band.GREEN
        if value < 50_000:
            return Band.YELLOW
        return Band.RED
    if metric_name == METRIC_ARI_WEEK:
        if value >= 0.7:
            return Band.GREEN
        if value >= 0.5:
            return Band.YELLOW
        return Band.RED
    return Band.INFO
