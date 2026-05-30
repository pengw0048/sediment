"""ARCH-2 drift metrics tests."""

from __future__ import annotations

from pke.evidence.models import iso_utc, new_ulid
from pke.maintenance.jobs import drift_metrics
from pke.quality.metrics import (
    METRIC_ARI_WEEK,
    METRIC_CENTROID_COUNT,
    METRIC_LLM_COST_30D,
    Band,
    band_for,
    latest_metric,
    record_metric,
)


def test_band_for_centroid_count_thresholds() -> None:
    """centroid_count is green below 25k, yellow up to 50k, red beyond."""
    assert band_for(METRIC_CENTROID_COUNT, 0) is Band.GREEN
    assert band_for(METRIC_CENTROID_COUNT, 24_999) is Band.GREEN
    assert band_for(METRIC_CENTROID_COUNT, 25_000) is Band.YELLOW
    assert band_for(METRIC_CENTROID_COUNT, 49_999) is Band.YELLOW
    assert band_for(METRIC_CENTROID_COUNT, 50_000) is Band.RED


def test_band_for_ari_week_thresholds() -> None:
    """ARI is green ≥ 0.7, yellow 0.5-0.7, red below."""
    assert band_for(METRIC_ARI_WEEK, 1.0) is Band.GREEN
    assert band_for(METRIC_ARI_WEEK, 0.7) is Band.GREEN
    assert band_for(METRIC_ARI_WEEK, 0.69) is Band.YELLOW
    assert band_for(METRIC_ARI_WEEK, 0.5) is Band.YELLOW
    assert band_for(METRIC_ARI_WEEK, 0.49) is Band.RED
    assert band_for(METRIC_ARI_WEEK, -0.1) is Band.RED


def test_band_for_unknown_metric_returns_info() -> None:
    """Unknown metric names land in the info band so readers do not crash."""
    assert band_for("brand_new_metric", 42.0) is Band.INFO
    assert band_for(METRIC_LLM_COST_30D, 0.0) is Band.INFO


def test_record_and_latest_round_trip(app) -> None:
    """record_metric persists; latest_metric reads back with the right band."""
    record_metric(app.sqlite, metric_name=METRIC_CENTROID_COUNT, value=12_345)
    snap = latest_metric(app.sqlite, metric_name=METRIC_CENTROID_COUNT)
    assert snap is not None
    assert snap.value == 12_345
    assert snap.band is Band.GREEN


def test_latest_metric_returns_most_recent_snapshot(app) -> None:
    """Multiple inserts: latest_metric returns the one with the newest recorded_at."""
    record_metric(app.sqlite, metric_name=METRIC_ARI_WEEK, value=0.30)
    record_metric(app.sqlite, metric_name=METRIC_ARI_WEEK, value=0.85)
    snap = latest_metric(app.sqlite, metric_name=METRIC_ARI_WEEK)
    assert snap is not None
    assert snap.value == 0.85
    assert snap.band is Band.GREEN


def test_run_centroid_count_persists_active_count(app) -> None:
    """run_centroid_count counts active skill_nodes and records the value."""
    for i in range(3):
        app.sqlite.conn.execute(
            """
            INSERT INTO skill_nodes(
              id, canonical_name, description, embedding, first_seen_at,
              last_seen_at, created_at, updated_at, user_status
            )
            VALUES (?, ?, '', x'', ?, ?, ?, ?, ?)
            """,
            (
                new_ulid(),
                f"skill-{i}",
                iso_utc(),
                iso_utc(),
                iso_utc(),
                iso_utc(),
                "active" if i < 2 else "dropped",
            ),
        )
    app.sqlite.conn.commit()
    count = drift_metrics.run_centroid_count(app.sqlite)
    assert count == 2
    snap = latest_metric(app.sqlite, metric_name=METRIC_CENTROID_COUNT)
    assert snap is not None
    assert snap.value == 2.0
    assert snap.band is Band.GREEN


def test_run_ari_week_returns_none_when_no_overlap(app) -> None:
    """ARI cannot be computed without overlapping candidates across the two windows."""
    ari = drift_metrics.run_ari_week(app.sqlite)
    assert ari is None


def test_run_llm_cost_30d_writes_zero_placeholder(app) -> None:
    """LLM cost placeholder records 0 with a note until call logging exists."""
    cost = drift_metrics.run_llm_cost_30d(app.sqlite)
    assert cost == 0.0
    snap = latest_metric(app.sqlite, metric_name=METRIC_LLM_COST_30D)
    assert snap is not None
    assert snap.value == 0.0
    assert snap.band is Band.INFO
    assert "llm_call_log" in str(snap.payload.get("note", ""))


def test_scheduler_registers_drift_metrics_entry() -> None:
    """drift_metrics shows up in default_job_entries."""
    from pke.maintenance.scheduler import default_job_entries

    names = {entry.name for entry in default_job_entries()}
    assert "drift_metrics" in names
