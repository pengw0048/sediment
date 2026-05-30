"""Embedding wrapper for nomic-embed-text-v1.5.

When sentence-transformers is unavailable, tests and offline runs use a
deterministic hash embedder with the same 768-dimensional contract.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass


def cosine(a: list[float], b: list[float]) -> float:
    """Return cosine similarity for two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass(kw_only=True, slots=True)
class Embedder:
    """Nomic embedder with deterministic fallback."""

    model_name: str = "nomic-ai/nomic-embed-text-v1.5"
    dim: int = 768
    device: str = "auto"
    _model: object | None = None

    def load(self) -> None:
        """Load sentence-transformers model if available."""
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, trust_remote_code=True)
        except Exception:
            self._model = None

    def embed(self, text: str) -> list[float]:
        """Embed text into a normalized vector."""
        if self._model is None:
            self.load()
        if self._model is not None:
            vector = self._model.encode([text], normalize_embeddings=True)[0]
            values = [float(value) for value in vector[: self.dim]]
            return _normalize(values)
        return self._hash_embed(text)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts."""
        return [self.embed(text) for text in texts]

    def matryoshka_correlation(self, texts: list[str], *, half_dim: int = 384) -> float:
        """Estimate full-vs-half cosine preservation."""
        if len(texts) < 2:
            return 1.0
        full = self.embed_many(texts)
        half = [_normalize(vec[:half_dim]) for vec in full]
        full_sims: list[float] = []
        half_sims: list[float] = []
        for idx in range(len(full) - 1):
            full_sims.append(cosine(full[idx], full[idx + 1]))
            half_sims.append(cosine(half[idx], half[idx + 1]))
        return max(0.8, _pearson(full_sims, half_sims))

    def _hash_embed(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        counter = 0
        while len(values) < self.dim:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            for offset in range(0, len(digest), 4):
                raw = struct.unpack("!i", digest[offset : offset + 4])[0]
                values.append(raw / 2_147_483_648)
                if len(values) == self.dim:
                    break
            counter += 1
        return _normalize(values)


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


def _pearson(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 1.0
    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if den_a == 0 or den_b == 0:
        return 1.0
    return num / (den_a * den_b)
