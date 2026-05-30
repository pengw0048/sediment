"""Disk-backed response cache for LLM calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pke.evidence.models import sha256_hex


@dataclass(kw_only=True, slots=True)
class ResponseCache:
    """Content-hash keyed JSON cache."""

    root: Path

    def get(self, key: str) -> dict[str, object] | None:
        """Return cached JSON if present."""
        path = self.root / f"{sha256_hex(key)}.json"
        if not path.exists():
            return None
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def put(self, key: str, value: dict[str, object]) -> None:
        """Write one cache entry."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{sha256_hex(key)}.json"
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
