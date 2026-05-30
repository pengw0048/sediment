"""Minimal SQL migration runner."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
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
    """Apply all pending migrations."""
    version = current_version(conn)
    for migration in load_migrations():
        if migration.version > version:
            conn.executescript(migration.up_sql)
            version = migration.version


def rollback(conn: sqlite3.Connection, *, steps: int = 1) -> None:
    """Rollback the most recent migration steps."""
    version = current_version(conn)
    migrations = {migration.version: migration for migration in load_migrations()}
    for target in range(version, max(0, version - steps), -1):
        migration = migrations.get(target)
        if migration is not None and migration.down_sql:
            conn.executescript(migration.down_sql)
