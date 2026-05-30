"""HNSW-compatible ANN index abstraction.

The production path can be swapped to hnswlib. This reference implementation is
an exact cosine search, which keeps unit tests deterministic and dependency
light.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pke.identity.embedder import cosine


@dataclass(kw_only=True, slots=True)
class AnnIndex:
    """Persistent cosine nearest-neighbor index."""

    dim: int = 768
    vectors: dict[str, list[float]] = field(default_factory=dict)

    def add(self, item_id: str, vector: list[float]) -> None:
        """Add or replace one vector."""
        if len(vector) != self.dim:
            raise ValueError("vector dimension mismatch")
        self.vectors[item_id] = vector

    def search(self, vector: list[float], *, k: int = 5) -> list[tuple[str, float]]:
        """Return top-k ids and cosine similarity."""
        scored = [
            (item_id, cosine(vector, candidate)) for item_id, candidate in self.vectors.items()
        ]
        return sorted(scored, key=lambda item: item[1], reverse=True)[:k]

    def save(self, path: Path) -> None:
        """Persist the index as JSON."""
        path.write_text(json.dumps({"dim": self.dim, "vectors": self.vectors}), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> AnnIndex:
        """Load a JSON index, or return an empty one."""
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            dim=int(raw["dim"]), vectors={str(k): list(v) for k, v in raw["vectors"].items()}
        )
