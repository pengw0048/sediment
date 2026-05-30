"""Review screen: single-item predict-before-reveal → answer → grade loop."""

from __future__ import annotations

import time
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static

from pke.tui.service import PreparedItem, grade_answer, start_session

TITLE = "Review"

_PHASE_PREDICT = "predict"
_PHASE_ANSWER = "answer"
_PHASE_REVEAL = "reveal"


class ReviewScreen(Screen[None]):
    """Walk through a session one item at a time.

    Phases per item:

    1. predict — show the prompt, ask the user to commit to an
       expected-mastery rating (1-4) BEFORE seeing the answer surface.
       Calibration uses this to compare predicted vs. observed grade.
    2. answer — accept the answer string and submit it.
    3. reveal — show the grade and feedback. Press space to advance.
    """

    BINDINGS = [
        Binding("space", "advance", "Next", show=True),
        Binding("enter", "submit", "Submit", show=True),
        Binding("1", "rate(1)", "Fail", show=False),
        Binding("2", "rate(2)", "Hard", show=False),
        Binding("3", "rate(3)", "Good", show=False),
        Binding("4", "rate(4)", "Easy", show=False),
        Binding("q", "leave", "Back", show=True),
    ]

    def __init__(self, app_ref: Any, *, limit: int = 5) -> None:
        super().__init__()
        self._app = app_ref
        self._limit = limit
        self._items: list[PreparedItem] = []
        self._cursor: int = 0
        self._phase: str = _PHASE_PREDICT
        self._self_rating: int = 0
        self._started_ms: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Static("loading session...", id="prompt")
            yield Static("", id="status")
            yield Input(placeholder="Your answer (press Enter to submit)", id="answer_input")
            yield Static("", id="feedback")
        yield Footer()

    async def on_mount(self) -> None:
        self._items = await start_session(self._app, limit=self._limit, client="tui")
        if not self._items:
            self.query_one("#prompt", Static).update("No due items. Press q to leave.")
            self.query_one("#status", Static).update("")
            self.query_one("#answer_input", Input).display = False
            return
        self._render_predict_phase()

    def _current(self) -> PreparedItem | None:
        if 0 <= self._cursor < len(self._items):
            return self._items[self._cursor]
        return None

    def _render_predict_phase(self) -> None:
        item = self._current()
        if item is None:
            return
        self._phase = _PHASE_PREDICT
        self._self_rating = 0
        self._started_ms = time.monotonic_ns() // 1_000_000
        self.query_one("#prompt", Static).update(
            f"[{self._cursor + 1}/{len(self._items)}] [{item.skill_name}]\n\n{item.prompt}"
        )
        self.query_one("#status", Static).update(
            "Predict your grade: 1=fail  2=hard  3=good  4=easy  (then Enter to commit)"
        )
        self.query_one("#feedback", Static).update("")
        answer_input = self.query_one("#answer_input", Input)
        answer_input.value = ""
        answer_input.display = False

    def _render_answer_phase(self) -> None:
        self._phase = _PHASE_ANSWER
        self.query_one("#status", Static).update(
            f"Self-prediction: {self._self_rating}. Type your answer and press Enter."
        )
        answer_input = self.query_one("#answer_input", Input)
        answer_input.display = True
        answer_input.focus()

    async def _render_reveal_phase(self, user_answer: str) -> None:
        item = self._current()
        if item is None:
            return
        elapsed = max(0, time.monotonic_ns() // 1_000_000 - self._started_ms)
        grade = await grade_answer(
            self._app,
            item=item,
            user_answer=user_answer,
            self_rating=self._self_rating,
            elapsed_ms=elapsed,
        )
        self._phase = _PHASE_REVEAL
        self.query_one("#answer_input", Input).display = False
        self.query_one("#status", Static).update(
            f"Graded: {grade.grade}  confidence={grade.confidence:.2f}"
        )
        self.query_one("#feedback", Static).update(
            (grade.feedback or "(no feedback)") + "\n\nPress space for the next item."
        )

    def action_rate(self, rating: str) -> None:
        if self._phase != _PHASE_PREDICT:
            return
        try:
            value = int(rating)
        except ValueError:
            return
        if 1 <= value <= 4:
            self._self_rating = value
            self.query_one("#status", Static).update(f"Selected prediction: {value}. Press Enter.")

    def action_submit(self) -> None:
        if self._phase == _PHASE_PREDICT:
            if self._self_rating == 0:
                self.query_one("#status", Static).update("Pick a 1-4 prediction first.")
                return
            self._render_answer_phase()
        elif self._phase == _PHASE_ANSWER:
            self.run_worker(
                self._render_reveal_phase(self.query_one("#answer_input", Input).value),
                exclusive=True,
            )

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        if self._phase == _PHASE_ANSWER:
            await self._render_reveal_phase(message.value)

    def action_advance(self) -> None:
        if self._phase != _PHASE_REVEAL:
            return
        self._cursor += 1
        if self._cursor >= len(self._items):
            self.query_one("#prompt", Static).update("Session complete. Press q to return.")
            self.query_one("#status", Static).update("")
            self.query_one("#feedback", Static).update("")
            self.query_one("#answer_input", Input).display = False
            return
        self._render_predict_phase()

    def action_leave(self) -> None:
        self.app.pop_screen()
