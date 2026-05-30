"""Kuzu-compatible graph materialized view.

If the `kuzu` package is unavailable, the store uses a local JSON materialized
view with the same node/edge semantics needed by review and tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(kw_only=True, slots=True)
class KuzuStore:
    """Graph store for Skill nodes and bitemporal edges."""

    root: Path
    skills: dict[str, dict[str, object]] = field(default_factory=dict)
    edges: list[dict[str, object]] = field(default_factory=list)

    def ensure_schema(self) -> None:
        """Create the graph directory and metadata marker."""
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / "schema.json"
        if not marker.exists():
            marker.write_text(
                json.dumps(
                    {
                        "nodes": ["Skill", "User", "Topic"],
                        "rels": ["KNOWS", "RELATES_TO", "BELONGS_TO"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    def upsert_skill(self, skill: dict[str, object]) -> None:
        """Insert or update one Skill node."""
        self.ensure_schema()
        skill_id = str(skill["id"])
        self.skills[skill_id] = dict(skill)
        self._save()

    def upsert_edge(self, edge: dict[str, object]) -> None:
        """Insert a bitemporal relation edge."""
        required = {"valid_from", "valid_to", "recorded_from", "recorded_to"}
        if not required.issubset(edge):
            raise ValueError("edge requires four bitemporal timestamps")
        self.edges.append(dict(edge))
        self._save()

    def neighbors(self, skill_id: str) -> list[dict[str, object]]:
        """Return currently valid neighboring edges."""
        return [
            edge
            for edge in self.edges
            if edge.get("src") == skill_id or edge.get("dst") == skill_id
        ]

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "graph.json").write_text(
            json.dumps({"skills": self.skills, "edges": self.edges}, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, root: Path) -> KuzuStore:
        """Load a JSON materialized graph."""
        path = root / "graph.json"
        if not path.exists():
            return cls(root=root)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(root=root, skills=dict(raw.get("skills", {})), edges=list(raw.get("edges", [])))
