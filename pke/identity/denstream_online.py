"""River DenStream wrapper for online identity micro-clustering.

:class:`pke.identity.resolver.IdentityResolver` calls
:meth:`OnlineClusterer.partial_fit` for every candidate it resolves and
stores the returned cluster id on the skill_candidates row, so downstream
batch-cluster and EDC sweeps can group candidates by micro-cluster
membership rather than recomputing pairwise cosines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from river.cluster import DenStream

from pke.config.settings import Settings


@dataclass(kw_only=True, slots=True)
class OnlineClusterer:
    """Thin wrapper around `river.cluster.DenStream`."""

    decaying_factor: float = 0.0006
    epsilon: float = 0.18
    beta: float = 0.75
    mu: float = 2.0
    n_samples_init: int = 1000
    stream_speed: int = 100
    model: Any = field(init=False)

    def __post_init__(self) -> None:
        self.model = DenStream(
            decaying_factor=self.decaying_factor,
            beta=self.beta,
            mu=self.mu,
            epsilon=self.epsilon,
            n_samples_init=self.n_samples_init,
            stream_speed=self.stream_speed,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> OnlineClusterer:
        """Create a DenStream wrapper from identity settings."""
        identity = settings.raw.get("identity", {})
        return cls(
            decaying_factor=float(identity.get("denstream_lambda", 0.0006)),
            epsilon=float(identity.get("denstream_eps", 0.18)),
            beta=float(identity.get("denstream_beta", 0.75)),
            mu=float(identity.get("denstream_mu", 2.0)),
        )

    def partial_fit(self, vector: list[float]) -> int:
        """Learn one vector and return River's current cluster assignment."""
        features = self._features(vector)
        self.model.learn_one(features)
        return int(self.model.predict_one(features))

    def assign(self, vector: list[float]) -> int:
        """Compatibility alias for the identity worker."""
        return self.partial_fit(vector)

    @property
    def micro_cluster_count(self) -> int:
        """Return DenStream's current number of clusters."""
        return int(self.model.n_clusters)

    @staticmethod
    def _features(vector: list[float]) -> dict[int, float]:
        return dict(enumerate(vector))
