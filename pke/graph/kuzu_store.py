"""Kuzu-backed graph materialized view."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import kuzu

from pke.graph.schema import bootstrap_kuzu


@dataclass(kw_only=True, slots=True)
class KuzuStore:
    """Graph store for Skill nodes and bitemporal edges."""

    root: Path
    database: kuzu.Database = field(init=False, repr=False)
    conn: kuzu.Connection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = kuzu.Database(str(self.root))
        self.conn = kuzu.Connection(self.database)

    def ensure_schema(self) -> None:
        """Create Kuzu graph tables."""
        bootstrap_kuzu(self.conn)

    def upsert_skill(self, skill: dict[str, object]) -> None:
        """Insert or update one Skill node."""
        self.ensure_schema()
        self.conn.execute(
            """
            MERGE (skill:Skill {id: $id})
            SET skill.canonical_name = $canonical_name,
                skill.description = $description,
                skill.embedding = $embedding,
                skill.cluster_size = $cluster_size,
                skill.first_seen_at = $first_seen_at,
                skill.last_seen_at = $last_seen_at,
                skill.user_status = $user_status
            """,
            {
                "id": str(skill["id"]),
                "canonical_name": str(skill.get("canonical_name", "")),
                "description": str(skill.get("description", "")),
                "embedding": _float_list(skill.get("embedding", [])),
                "cluster_size": _int_value(skill.get("cluster_size", 1)),
                "first_seen_at": _timestamp(skill.get("first_seen_at")),
                "last_seen_at": _timestamp(skill.get("last_seen_at")),
                "user_status": str(skill.get("user_status", "active")),
            },
        )

    def upsert_edge(self, edge: dict[str, object]) -> None:
        """Insert a bitemporal relation edge."""
        required = {"t_valid_start", "t_valid_end", "t_observed_start", "t_observed_end"}
        if not required.issubset(edge):
            raise ValueError("edge requires four bitemporal timestamps")
        self.ensure_schema()
        self.conn.execute("MERGE (skill:Skill {id: $id})", {"id": str(edge["src"])})
        self.conn.execute("MERGE (skill:Skill {id: $id})", {"id": str(edge["dst"])})
        self.conn.execute(
            """
            MATCH (src:Skill), (dst:Skill)
            WHERE src.id = $src AND dst.id = $dst
            CREATE (src)-[:RELATES_TO]->(dst)
            """,
            {"src": str(edge["src"]), "dst": str(edge["dst"])},
        )
        self._set_latest_edge_properties(edge)

    def neighbors(self, skill_id: str) -> list[dict[str, object]]:
        """Return neighboring RELATES_TO edges."""
        self.ensure_schema()
        rows = self.conn.execute(
            """
            MATCH (src:Skill)-[rel:RELATES_TO]->(dst:Skill)
            WHERE src.id = $skill_id OR dst.id = $skill_id
            RETURN src.id AS src, dst.id AS dst, rel.relation_type AS relation_type,
                   rel.strength AS strength, rel.source AS source,
                   rel.t_valid_start AS t_valid_start, rel.t_valid_end AS t_valid_end,
                   rel.t_observed_start AS t_observed_start,
                   rel.t_observed_end AS t_observed_end, rel.created_at AS created_at
            """,
            {"skill_id": skill_id},
        ).get_as_df()
        return [_row_to_edge(row) for row in rows.to_dict("records")]

    @property
    def edges(self) -> list[dict[str, object]]:
        """Return all relation edges for debug and tests."""
        self.ensure_schema()
        rows = self.conn.execute(
            """
            MATCH (src:Skill)-[rel:RELATES_TO]->(dst:Skill)
            RETURN src.id AS src, dst.id AS dst, rel.relation_type AS relation_type,
                   rel.strength AS strength, rel.source AS source,
                   rel.t_valid_start AS t_valid_start, rel.t_valid_end AS t_valid_end,
                   rel.t_observed_start AS t_observed_start,
                   rel.t_observed_end AS t_observed_end, rel.created_at AS created_at
            """
        ).get_as_df()
        return [_row_to_edge(row) for row in rows.to_dict("records")]

    @classmethod
    def load(cls, root: Path) -> KuzuStore:
        """Open a Kuzu graph directory."""
        return cls(root=root)

    def _set_latest_edge_properties(self, edge: dict[str, object]) -> None:
        params: dict[str, object] = {
            "src": str(edge["src"]),
            "dst": str(edge["dst"]),
            "relation_type": str(edge["relation_type"]),
            "strength": _float_value(edge["strength"]),
            "source": str(edge["source"]),
            "t_valid_start": _timestamp(edge["t_valid_start"]),
            "t_observed_start": _timestamp(edge["t_observed_start"]),
            "created_at": _timestamp(edge["created_at"]),
        }
        set_clauses = [
            "rel.relation_type = $relation_type",
            "rel.strength = $strength",
            "rel.source = $source",
            "rel.t_valid_start = $t_valid_start",
            "rel.t_observed_start = $t_observed_start",
            "rel.created_at = $created_at",
        ]
        if edge.get("t_valid_end") is not None:
            params["t_valid_end"] = _timestamp(edge["t_valid_end"])
            set_clauses.append("rel.t_valid_end = $t_valid_end")
        if edge.get("t_observed_end") is not None:
            params["t_observed_end"] = _timestamp(edge["t_observed_end"])
            set_clauses.append("rel.t_observed_end = $t_observed_end")
        self.conn.execute(
            f"""
            MATCH (src:Skill)-[rel:RELATES_TO]->(dst:Skill)
            WHERE src.id = $src AND dst.id = $dst AND rel.created_at IS NULL
            SET {", ".join(set_clauses)}
            """,
            params,
        )


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if value is None:
        return datetime.now(tz=UTC)
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)


def _float_list(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    return [_float_value(item) for item in value]


def _float_value(value: object) -> float:
    if not isinstance(value, int | float | str):
        raise TypeError(f"expected numeric value, got {type(value).__name__}")
    return float(value)


def _int_value(value: object) -> int:
    if not isinstance(value, int | float | str):
        raise TypeError(f"expected integer value, got {type(value).__name__}")
    return int(value)


def _row_to_edge(row: dict[str, Any]) -> dict[str, object]:
    return {
        "src": row["src"],
        "dst": row["dst"],
        "relation_type": row["relation_type"],
        "strength": row["strength"],
        "source": row["source"],
        "t_valid_start": row["t_valid_start"],
        "t_valid_end": _none_if_missing(row["t_valid_end"]),
        "t_observed_start": row["t_observed_start"],
        "t_observed_end": _none_if_missing(row["t_observed_end"]),
        "created_at": row["created_at"],
    }


def _none_if_missing(value: object) -> object | None:
    return None if str(value) == "NaT" else value
