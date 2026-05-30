"""Online micro-clusterer compatible with river DenStream semantics."""

from __future__ import annotations

from dataclasses import dataclass, field

from pke.identity.embedder import cosine


@dataclass(kw_only=True, slots=True)
class MicroCluster:
    """One online micro-cluster."""

    centroid: list[float]
    weight: int = 1

    def update(self, vector: list[float]) -> None:
        """Update centroid with one vector."""
        self.weight += 1
        self.centroid = [
            old + (new - old) / self.weight for old, new in zip(self.centroid, vector, strict=True)
        ]


@dataclass(kw_only=True, slots=True)
class OnlineClusterer:
    """Small DenStream-style wrapper with bounded micro-cluster growth."""

    eps: float = 0.18
    max_clusters: int = 500
    clusters: list[MicroCluster] = field(default_factory=list)

    def partial_fit(self, vector: list[float]) -> int:
        """Assign a vector to a micro-cluster and return cluster index."""
        if not self.clusters:
            self.clusters.append(MicroCluster(centroid=vector))
            return 0
        distances = [
            (idx, 1.0 - cosine(vector, cluster.centroid))
            for idx, cluster in enumerate(self.clusters)
        ]
        idx, distance = min(distances, key=lambda item: item[1])
        if distance <= self.eps or len(self.clusters) >= self.max_clusters:
            self.clusters[idx].update(vector)
            return idx
        self.clusters.append(MicroCluster(centroid=vector))
        return len(self.clusters) - 1

    @property
    def micro_cluster_count(self) -> int:
        """Return the current micro-cluster count."""
        return len(self.clusters)
