"""Today screen: scored due review items in a DataTable."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from pke.tui.service import fetch_today

TITLE = "Today"


class TodayScreen(Screen[None]):
    """List of today's top-K candidates, ranked by ItemSelector."""

    BINDINGS = [
        Binding("r", "start_review", "Review", show=True),
        Binding("enter", "start_review", "Review", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("q", "app.quit", "Quit", show=True),
    ]

    def __init__(self, app_ref: Any, *, limit: int = 10) -> None:
        super().__init__()
        self._app = app_ref
        self._limit = limit

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(f"Top {self._limit} candidates for review. Press r to start.", id="hint")
        yield DataTable(id="today_table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#today_table", DataTable)
        table.add_columns("skill", "score", "unaided", "reps", "outsource_7d", "reason")
        rows = fetch_today(self._app, limit=self._limit)
        if not rows:
            table.add_row("(no due items)", "", "", "", "", "")
            return
        for row in rows:
            table.add_row(
                row.name[:60],
                f"{row.score:.3f}",
                f"{row.unaided:.2f}",
                str(row.reps),
                str(row.outsource_7d),
                row.reason,
            )

    def action_cursor_down(self) -> None:
        self.query_one("#today_table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#today_table", DataTable).action_cursor_up()

    def action_start_review(self) -> None:
        from pke.tui.screens.review import ReviewScreen

        self.app.push_screen(ReviewScreen(self._app, limit=self._limit))
