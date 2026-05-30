"""Evidence screen: most recent evidence_events rows."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header

TITLE = "Evidence"


class EvidenceScreen(Screen[None]):
    """Browse the most recent evidence events ingested into Sediment."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("q", "app.pop_screen", "Back", show=True),
    ]

    def __init__(self, app_ref: Any, *, limit: int = 100) -> None:
        super().__init__()
        self._app = app_ref
        self._limit = limit

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="evidence_table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#evidence_table", DataTable)
        table.add_columns("observed_at", "source", "kind", "summary")
        rows = self._app.sqlite.conn.execute(
            """
            SELECT observed_at, source, kind, summary
            FROM evidence_events
            ORDER BY observed_at DESC
            LIMIT ?
            """,
            (self._limit,),
        ).fetchall()
        if not rows:
            table.add_row("(no evidence yet)", "", "", "")
            return
        for row in rows:
            table.add_row(
                str(row["observed_at"])[:19],
                str(row["source"])[:20],
                str(row["kind"])[:20],
                str(row["summary"] or "")[:80],
            )

    def action_cursor_down(self) -> None:
        self.query_one("#evidence_table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#evidence_table", DataTable).action_cursor_up()
