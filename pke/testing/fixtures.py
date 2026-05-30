"""Reusable test fixtures."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from pke.app import App
from pke.config.settings import Settings


@contextmanager
def temp_app() -> Iterator[App]:
    """Yield an App backed by a temporary data directory."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(
            data_dir=root / "data",
            config_path=root / "config.toml",
            intervention_per_source={},
        )
        app = App.create(settings=settings)
        try:
            yield app
        finally:
            app.close()


@dataclass(kw_only=True, slots=True)
class MockLLMClient:
    """Small deterministic LLM fixture for unit tests.

    The mock dispatches by inspecting the system prompt for distinctive
    tokens. This keeps the fixture single-source-of-truth for unit tests
    that exercise different LLM call sites (extraction, judging, ...).
    """

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        if "review-answer judge" in system or "Item prompt:" in user:
            return self._judge(system=system, user=user)
        return self._extract(system=system, user=user)

    def _extract(self, *, system: str, user: str) -> dict[str, object]:
        confidence = 0.8 if system else 0.5
        return {
            "rationale": "mock extraction",
            "skills": [
                {
                    "name": "fastapi routes",
                    "description": user[:80],
                    "polarity": "asked-about" if "?" in user else "demonstrated",
                    "confidence": confidence,
                    "span_start": 0,
                    "span_end": min(len(user), 80),
                }
            ],
        }

    def _judge(self, *, system: str, user: str) -> dict[str, object]:
        # Treat a non-empty user answer over 40 chars as "pass", shorter as
        # "partial", and an empty answer as "fail". Confidence depends on
        # whether the rubric mentioned a pass criterion.
        marker = "User answer:"
        body = user.split(marker, 1)[-1].strip() if marker in user else user
        if len(body) > 60:
            grade = "pass"
        elif len(body) > 10:
            grade = "partial"
        else:
            grade = "fail"
        confidence = 0.85 if "pass:" in user and "pass:" not in system else 0.7
        return {
            "grade": grade,
            "confidence": confidence,
            "feedback": f"mock judge grade={grade}",
        }
