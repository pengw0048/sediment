"""Half-Life Regression implementation for recall probability."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(kw_only=True, slots=True)
class HLR:
    """Small HLR model using p = 2^(-delta / h), h = exp(theta dot x)."""

    theta: list[float] = field(default_factory=lambda: [math.log(24.0)])

    def halflife(self, features: list[float]) -> float:
        """Return half-life in hours."""
        if len(features) != len(self.theta):
            raise ValueError("feature dimension mismatch")
        dot = sum(t * x for t, x in zip(self.theta, features, strict=True))
        return math.exp(dot)

    def recall_probability(self, *, delta_hours: float, features: list[float]) -> float:
        """Return p = 2^(-delta / h)."""
        h = max(1e-9, self.halflife(features))
        return 2 ** (-delta_hours / h)

    def update_halflife(self, *, halflife_h: float, grade: str) -> float:
        """Update a scalar half-life using review grade."""
        factor = {"pass": 2.0, "partial": 1.3, "fail": 0.5}.get(grade, 1.0)
        return min(365 * 24.0, max(24.0, halflife_h * factor))
