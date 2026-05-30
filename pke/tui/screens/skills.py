"""Skills screen: list of canonical skills with mastery snapshots."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header

TITLE = "Skills"


class SkillsScreen(Screen[None]):
    """Browse the active skill graph alongside its mastery rows."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("q", "app.pop_screen", "Back", show=True),
    ]

    def __init__(self, app_ref: Any, *, limit: int = 200) -> None:
        super().__init__()
        self._app = app_ref
        self._limit = limit

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="skills_table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#skills_table", DataTable)
        table.add_columns("name", "unaided", "functional", "reps", "outsource_7d")
        rows = self._app.sqlite.conn.execute(
            """
            SELECT s.canonical_name,
                   m.unaided_retrievability, m.functional_retrievability,
                   m.unaided_reps, m.outsource_count_7d
            FROM skill_nodes s
            JOIN skill_mastery_state m ON m.skill_id = s.id
            WHERE s.user_status = 'active'
            ORDER BY m.unaided_retrievability ASC
            LIMIT ?
            """,
            (self._limit,),
        ).fetchall()
        if not rows:
            table.add_row("(no skills)", "", "", "", "")
            return
        for row in rows:
            table.add_row(
                str(row["canonical_name"])[:60],
                f"{float(row['unaided_retrievability'] or 0.0):.2f}",
                f"{float(row['functional_retrievability'] or 0.0):.2f}",
                str(int(row["unaided_reps"] or 0)),
                str(int(row["outsource_count_7d"] or 0)),
            )

    def action_cursor_down(self) -> None:
        self.query_one("#skills_table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#skills_table", DataTable).action_cursor_up()
