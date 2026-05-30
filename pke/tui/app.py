"""Textual TUI for Sediment.

Two real screens (today, review) plus two read-only browsers (skills,
evidence). All four hit the same code path the HTTP API does — start a
session, generate items via :class:`ItemGenerator`, grade answers via
:class:`Grader` — so the TUI and the web client cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from pke.app import App


def run() -> None:
    """Run the terminal review UI, falling back to a plain-text notice."""
    try:
        from textual.app import App as TextualApp
        from textual.binding import Binding
    except ImportError:
        print("Sediment review ready. Install textual for the full TUI.")
        return

    from pke.tui.screens.evidence import EvidenceScreen
    from pke.tui.screens.skills import SkillsScreen
    from pke.tui.screens.today import TodayScreen

    class PKEApp(TextualApp[None]):
        """Top-level Textual app that owns a live :class:`App` and switches screens."""

        BINDINGS = [
            Binding("q", "quit", "Quit", show=True),
            Binding("1", "show_today", "Today", show=True),
            Binding("2", "show_review", "Review", show=True),
            Binding("3", "show_skills", "Skills", show=True),
            Binding("4", "show_evidence", "Evidence", show=True),
        ]

        def __init__(self, app_ref: Any) -> None:
            super().__init__()
            self._app = app_ref

        def on_mount(self) -> None:
            self.push_screen(TodayScreen(self._app))

        def action_show_today(self) -> None:
            self._swap_to(TodayScreen(self._app))

        def action_show_review(self) -> None:
            from pke.tui.screens.review import ReviewScreen

            self._swap_to(ReviewScreen(self._app))

        def action_show_skills(self) -> None:
            self._swap_to(SkillsScreen(self._app))

        def action_show_evidence(self) -> None:
            self._swap_to(EvidenceScreen(self._app))

        def _swap_to(self, screen: Any) -> None:
            while len(self.screen_stack) > 1:
                self.pop_screen()
            self.push_screen(screen)

    app = App.create()
    try:
        PKEApp(app).run()
    finally:
        app.close()
