"""Minimal SQL migration runner."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_RE = re.compile(r"^(\d{4})_.*\.sql$")


@dataclass(frozen=True, kw_only=True, slots=True)
class Migration:
    """Parsed migration file."""

    version: int
    path: Path
    up_sql: str
    down_sql: str


def load_migrations() -> list[Migration]:
    """Load migration files sorted by version."""
    migrations: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = MIGRATION_RE.match(path.name)
        if not match:
            continue
        text = path.read_text(encoding="utf-8")
        parts = text.split("-- down", maxsplit=1)
        up = parts[0].replace("-- up", "", 1).strip()
        down = parts[1].strip() if len(parts) == 2 else ""
        migrations.append(
            Migration(version=int(match.group(1)), path=path, up_sql=up, down_sql=down)
        )
    return migrations


def current_version(conn: sqlite3.Connection) -> int:
    """Return the latest applied migration version."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT max(version) AS version FROM schema_version").fetchone()
    value = row["version"] if row is not None else None
    return int(value or 0)


def apply_pending(conn: sqlite3.Connection) -> None:
    """Apply all pending migrations and record each in ``schema_version``.

    Without the schema_version write, the runner cannot detect what has
    already been applied — a second migration would silently re-run the
    first. Idempotent CREATE TABLE IF NOT EXISTS hides the bug until the
    second migration ships, which is why this is enforced from day 1.
    """
    version = current_version(conn)
    applied_at = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    for migration in load_migrations():
        if migration.version > version:
            conn.executescript(migration.up_sql)
            # Some legacy migrations write their own schema_version row inside
            # the .sql file (idempotent INSERT OR IGNORE). Use OR IGNORE here
            # too so we never double-insert and so a pre-2026 DB that already
            # has version=1 keeps working.
            conn.execute(
                "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
                (migration.version, applied_at),
            )
            conn.commit()
            version = migration.version


def rollback(conn: sqlite3.Connection, *, steps: int = 1) -> None:
    """Rollback the most recent migration steps."""
    version = current_version(conn)
    migrations = {migration.version: migration for migration in load_migrations()}
    for target in range(version, max(0, version - steps), -1):
        migration = migrations.get(target)
        if migration is not None and migration.down_sql:
            conn.executescript(migration.down_sql)
            conn.execute("DELETE FROM schema_version WHERE version = ?", (target,))
            conn.commit()
