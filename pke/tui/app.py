"""Textual TUI app."""

from __future__ import annotations


def run() -> None:
    """Run the terminal review UI, falling back to plain text."""
    try:
        from textual.app import App as TextualApp
        from textual.widgets import Footer, Header, Static

        class PKEApp(TextualApp[None]):
            """Minimal Textual review app."""

            BINDINGS = [("q", "quit", "Quit"), ("r", "review", "Review")]

            def compose(self):
                yield Header()
                yield Static("Sediment review ready. Press r to review, q to quit.")
                yield Footer()

        PKEApp().run()
    except Exception:
        print("Sediment review ready. Install textual for the full TUI.")
