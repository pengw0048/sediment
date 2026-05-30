"""Drift metrics (ARCH-2).

Owns the read/write helpers around ``quality_metrics``, the green/yellow/
red banding rules for each tracked metric, and the routines that
populate the table from the daemon's scheduler. Three metrics are
tracked:

* ``centroid_count`` — number of active skill nodes. Sampled daily.
  green < 25k, yellow 25k-50k, red ≥ 50k.
* ``ari_week`` — adjusted Rand index of this week's identity
  clustering against last week's. Sampled weekly. green ≥ 0.7,
  yellow 0.5-0.7, red < 0.5.
* ``llm_cost_30d`` — rolling 30-day LLM token cost in USD. Sampled
  weekly. No automatic action; the band column reports "info" for now.

The ARCH-2 spec also calls for auto-action (leaky-bucket throttling
when ARI lands red). The auto-action wiring lives next to the identity
resolver's bucket logic; this module owns the *measurement* side, not
the *reaction* side.
"""

from __future__ import annotations

from pke.quality.metrics import (
    Band,
    MetricSnapshot,
    band_for,
    latest_metric,
    record_metric,
)

__all__ = [
    "Band",
    "MetricSnapshot",
    "band_for",
    "latest_metric",
    "record_metric",
]
