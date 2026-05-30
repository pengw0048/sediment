"""Half-Life Regression for skill recall probability.

The model: ``p = 2 ** (-delta_hours / halflife)`` where
``halflife = exp(theta · features)``. ``theta`` is a 1D weight vector
fitted from observed reviews (offline, via :meth:`HLR.fit`); ``features``
is a fixed-length vector built from a skill's mastery state and the
dimension under update.

The feature names below are the contract; reorder or rename only by
shipping a migration that re-fits ``theta`` against the new layout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Feature layout. Index order matters: the model serializes theta as a
# flat list keyed by position, and re-ordering this tuple silently
# remaps coefficients.
FEATURE_NAMES: tuple[str, ...] = (
    "bias",
    "log_reps",
    "recent_pass_rate",
    "log_days_since_first_seen",
    "log_stability",
    "difficulty",
    "is_functional",
    "has_parent",
)


def _default_theta() -> list[float]:
    """Return the cold-start ``theta`` vector.

    At all-zero features (only ``bias=1``) the halflife evaluates to
    ``exp(log(24)) = 24`` hours.
    """
    return [
        math.log(24.0),  # bias
        0.30,  # log_reps
        0.50,  # recent_pass_rate
        0.10,  # log_days_since_first_seen
        0.50,  # log_stability
        -0.05,  # difficulty
        0.20,  # is_functional
        0.10,  # has_parent
    ]


@dataclass(kw_only=True, slots=True)
class HLR:
    """Half-Life Regression model with offline fit."""

    theta: list[float] = field(default_factory=_default_theta)

    @property
    def n_features(self) -> int:
        """Dimensionality of the feature vector this model expects."""
        return len(self.theta)

    def halflife(self, features: list[float]) -> float:
        """Return predicted halflife in hours."""
        if len(features) != len(self.theta):
            raise ValueError(
                f"feature dim {len(features)} != theta dim {len(self.theta)}; "
                f"expected names {FEATURE_NAMES}"
            )
        dot = sum(t * x for t, x in zip(self.theta, features, strict=True))
        return math.exp(dot)

    def recall_probability(self, *, delta_hours: float, features: list[float]) -> float:
        """Return ``2 ** (-delta_hours / halflife(features))``."""
        h = max(1e-9, self.halflife(features))
        return 2 ** (-delta_hours / h)

    def fit(
        self,
        samples: list[tuple[list[float], float, bool]],
        *,
        l2: float = 1e-3,
        max_iter: int = 200,
    ) -> None:
        """Fit ``theta`` to ``(features, delta_hours, was_recalled)`` samples.

        Minimizes log loss over ``p = 2 ** (-delta / h)`` with L2
        regularization, via scipy's L-BFGS-B. A zero-sample call is a
        no-op so a maintenance job can call ``fit`` unconditionally.
        """
        if not samples:
            return

        import numpy as np
        from scipy.optimize import minimize

        x_mat = np.array([row[0] for row in samples], dtype=np.float64)
        deltas = np.array([row[1] for row in samples], dtype=np.float64)
        y = np.array([1.0 if row[2] else 0.0 for row in samples], dtype=np.float64)
        if x_mat.shape[1] != len(self.theta):
            raise ValueError(
                f"sample feature dim {x_mat.shape[1]} != theta dim {len(self.theta)}"
            )

        ln2 = math.log(2.0)

        def loss(theta_vec: "np.ndarray[Any, np.dtype[np.float64]]") -> float:  # noqa: UP037
            half = np.exp(x_mat @ theta_vec)
            p = np.exp(-deltas / np.maximum(half, 1e-9) * ln2)
            p = np.clip(p, 1e-9, 1.0 - 1e-9)
            log_loss = -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)).mean()
            return float(log_loss + l2 * float(np.dot(theta_vec, theta_vec)))

        result = minimize(
            loss,
            np.array(self.theta, dtype=np.float64),
            method="L-BFGS-B",
            options={"maxiter": max_iter},
        )
        self.theta = [float(value) for value in result.x]

    def update_halflife(self, *, halflife_h: float, grade: str) -> float:
        """Per-review halflife adjustment used by the online mastery updater.

        Multiplicative SM-2-style step: passes stretch the interval,
        fails compress it. The HLR feature-based predictor governs
        long-run shape; this rule moves the current halflife into the
        next review interval until enough labels accumulate to retrain
        ``theta``. Clamped to ``[24h, 365 days]``.
        """
        factor = {"pass": 2.0, "partial": 1.3, "fail": 0.5}.get(grade, 1.0)
        return min(365 * 24.0, max(24.0, halflife_h * factor))


def extract_features(
    row: dict[str, object],
    *,
    dimension: str,
    has_parent: bool = False,
    recent_pass_rate: float = 0.5,
) -> list[float]:
    """Build the :data:`FEATURE_NAMES`-aligned feature vector for a row.

    ``row`` is a ``skill_mastery_state`` row (sqlite ``Row`` or dict).
    ``dimension`` is ``"unaided"`` or ``"functional"``. ``has_parent``
    comes from the caller because the mastery row does not carry the
    hierarchy edge. ``recent_pass_rate`` defaults to the prior of 0.5;
    the caller passes a real rolling estimate once review history is
    available.
    """
    reps = _as_float(_row_get(row, f"{dimension}_reps"))
    stability = _as_float(_row_get(row, f"{dimension}_stability"))
    difficulty = _as_float(_row_get(row, f"{dimension}_difficulty"))
    first_seen_days = _days_since_first_seen(row)
    return [
        1.0,
        math.log(reps + 1.0),
        max(0.0, min(1.0, recent_pass_rate)),
        math.log(first_seen_days + 1.0),
        math.log(stability + 1.0),
        max(0.0, min(10.0, difficulty)),
        1.0 if dimension == "functional" else 0.0,
        1.0 if has_parent else 0.0,
    ]


def _days_since_first_seen(row: dict[str, object]) -> float:
    """Days-since-first-seen estimate from a mastery row.

    Uses the ``unaided_reps`` count as a proxy until a first-seen
    timestamp is added to the schema. Returns ``0.0`` for unseen skills,
    clamped at 365 days.
    """
    return min(365.0, _as_float(_row_get(row, "unaided_reps")))


def _row_get(row: dict[str, object], key: str) -> object:
    """Read a value from a sqlite3 Row or plain dict by column name."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _as_float(value: object) -> float:
    """Coerce a sqlite/json scalar into ``float``, defaulting to ``0.0``."""
    if value is None:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0
