#!/usr/bin/env python3
"""Binary acceptance verifier for Sediment v1 local implementation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pke.adapters.manual_cli import build_manual_event
from pke.testing import temp_app


def check(condition: bool, label: str) -> None:
    """Raise if a binary check fails."""
    if not condition:
        raise SystemExit(f"FAIL: {label}")
    print(f"OK: {label}")


def main() -> None:
    """Run a local acceptance subset that does not require external accounts."""
    with temp_app() as app:
        columns = {
            row["name"]
            for row in app.sqlite.conn.execute("PRAGMA table_info(evidence_events)").fetchall()
        }
        check({"occurred_at", "ingested_at", "content_hash"}.issubset(columns), "Layer 1 schema")
        app.evidence.add(build_manual_event(user="append-only verifier"))
        try:
            app.sqlite.conn.execute("DELETE FROM evidence_events")
        except sqlite3.IntegrityError:
            append_only = True
        else:
            append_only = False
        check(append_only, "Layer 1 append-only delete trigger")
    for path in [
        "pke/extraction/prompts/extract_skills.j2",
        "pke/identity/embedder.py",
        "pke/graph/kuzu_store.py",
        "pke/mastery/hlr.py",
        "pke-ext/manifest.json",
        "docs/launch/show_hn.md",
    ]:
        check(Path(path).exists(), path)
    blocker = Path("BLOCKER.md")
    check(
        blocker.exists() and blocker.read_text(encoding="utf-8").strip() == "", "BLOCKER.md clear"
    )


if __name__ == "__main__":
    main()
