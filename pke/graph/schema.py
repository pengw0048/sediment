"""Kuzu schema bootstrap for the Sediment graph materialized view."""

from __future__ import annotations

KUZU_SCHEMA = (
    """
    CREATE NODE TABLE IF NOT EXISTS Skill (
      id              STRING,
      canonical_name  STRING,
      description     STRING,
      embedding       DOUBLE[],
      cluster_size    INT64,
      first_seen_at   TIMESTAMP,
      last_seen_at    TIMESTAMP,
      user_status     STRING,
      PRIMARY KEY (id)
    );
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS User (
      id           STRING,
      created_at   TIMESTAMP,
      PRIMARY KEY (id)
    );
    """,
    """
    CREATE REL TABLE IF NOT EXISTS KNOWS (
      FROM User TO Skill,
      functional      DOUBLE,
      unaided         DOUBLE,
      last_review_at  TIMESTAMP,
      reps            INT64
    );
    """,
    """
    CREATE REL TABLE IF NOT EXISTS RELATES_TO (
      FROM Skill TO Skill,
      relation_type      STRING,
      strength           DOUBLE,
      source             STRING,
      t_valid_start      TIMESTAMP,
      t_valid_end        TIMESTAMP,
      t_observed_start   TIMESTAMP,
      t_observed_end     TIMESTAMP,
      created_at         TIMESTAMP
    );
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS Topic (
      id          STRING,
      label       STRING,
      created_at  TIMESTAMP,
      PRIMARY KEY (id)
    );
    """,
    """
    CREATE REL TABLE IF NOT EXISTS BELONGS_TO (
      FROM Skill TO Topic,
      weight       DOUBLE,
      computed_at  TIMESTAMP
    );
    """,
)


def bootstrap_kuzu(conn: object) -> None:
    """Create all graph tables idempotently."""
    for statement in KUZU_SCHEMA:
        conn.execute(statement)
