"""Persistent hnswlib ANN index for skill identity search."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import hnswlib
import numpy as np
import numpy.typing as npt

DEFAULT_INDEX_PATH = Path("~/.local/share/pke/hnsw.bin").expanduser()


@dataclass(kw_only=True, slots=True)
class AnnIndex:
    """HNSW cosine nearest-neighbor index."""

    dim: int = 768
    m: int = 32
    ef_construction: int = 200
    ef_search: int = 64
    max_elements: int = 1024
    _index: hnswlib.Index = field(init=False, repr=False)
    _label_by_id: dict[str, int] = field(default_factory=dict)
    _id_by_label: dict[int, str] = field(default_factory=dict)
    _next_label: int = 1
    _initialized: bool = False

    def __post_init__(self) -> None:
        self._index = hnswlib.Index(space="cosine", dim=self.dim)

    def add(self, item_id: str, vector: list[float]) -> None:
        """Add or replace one vector in the HNSW index."""
        if len(vector) != self.dim:
            raise ValueError("vector dimension mismatch")
        self._ensure_index()
        label = self._label_by_id.get(item_id)
        if label is None:
            label = self._next_label
            self._next_label += 1
            self._label_by_id[item_id] = label
            self._id_by_label[label] = item_id
        self._ensure_capacity()
        self._index.add_items(self._as_array(vector), np.array([label], dtype=np.uint64))

    def search(self, vector: list[float], *, k: int = 5) -> list[tuple[str, float]]:
        """Return top-k ids and cosine similarity from hnswlib."""
        if len(vector) != self.dim:
            raise ValueError("vector dimension mismatch")
        if not self._initialized or self._index.get_current_count() == 0:
            return []
        labels, distances = self._index.knn_query(
            self._as_array(vector),
            k=min(k, self._index.get_current_count()),
        )
        return [
            (self._id_by_label[int(label)], 1.0 - float(distance))
            for label, distance in zip(labels[0], distances[0], strict=True)
        ]

    def save(self, path: Path | None = None) -> None:
        """Persist the HNSW index to `.bin` plus a small id-map sidecar."""
        target = path or DEFAULT_INDEX_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_index()
        self._index.save_index(str(target))
        self._metadata_path(target).write_text(
            json.dumps(
                {
                    "dim": self.dim,
                    "m": self.m,
                    "ef_construction": self.ef_construction,
                    "ef_search": self.ef_search,
                    "max_elements": self.max_elements,
                    "label_by_id": self._label_by_id,
                    "next_label": self._next_label,
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path | None = None) -> AnnIndex:
        """Load a persisted hnswlib index, or return an empty one."""
        target = path or DEFAULT_INDEX_PATH
        metadata_path = cls._metadata_path(target)
        if not target.exists() or not metadata_path.exists():
            return cls()
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        index = cls(
            dim=int(raw["dim"]),
            m=int(raw["m"]),
            ef_construction=int(raw["ef_construction"]),
            ef_search=int(raw["ef_search"]),
            max_elements=int(raw["max_elements"]),
        )
        index._label_by_id = {
            str(item_id): int(label) for item_id, label in raw["label_by_id"].items()
        }
        index._id_by_label = {label: item_id for item_id, label in index._label_by_id.items()}
        index._next_label = int(raw["next_label"])
        index._index.load_index(str(target), max_elements=index.max_elements)
        index._index.set_ef(index.ef_search)
        index._initialized = True
        return index

    def _ensure_index(self) -> None:
        if self._initialized:
            return
        self._index.init_index(
            max_elements=self.max_elements,
            M=self.m,
            ef_construction=self.ef_construction,
        )
        self._index.set_ef(self.ef_search)
        self._initialized = True

    def _ensure_capacity(self) -> None:
        if self._index.get_current_count() < self.max_elements:
            return
        self.max_elements *= 2
        self._index.resize_index(self.max_elements)

    def _as_array(self, vector: list[float]) -> npt.NDArray[np.float32]:
        return np.asarray([vector], dtype=np.float32)

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_suffix(f"{path.suffix}.ids.json")
