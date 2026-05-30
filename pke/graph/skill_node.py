"""Skill node graph synchronization helpers."""

from __future__ import annotations

from pke.db.sqlite import SQLiteStore
from pke.graph.kuzu_store import KuzuStore
from pke.identity.resolver import blob_to_vector


def sync_skill(sqlite: SQLiteStore, graph: KuzuStore, skill_id: str) -> None:
    """Mirror a SQLite skill_node row into the graph view."""
    row = sqlite.conn.execute("SELECT * FROM skill_nodes WHERE id = ?", (skill_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown skill {skill_id}")
    graph.upsert_skill(
        {
            "id": row["id"],
            "canonical_name": row["canonical_name"],
            "description": row["description"],
            "embedding": blob_to_vector(row["embedding"]),
            "cluster_size": row["cluster_size"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "user_status": row["user_status"],
        }
    )
