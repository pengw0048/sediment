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
    """Small deterministic LLM fixture for unit tests."""

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        confidence = 0.8 if system else 0.5
        return {
            "skills": [
                {
                    "name": "fastapi routes",
                    "description": user[:80],
                    "polarity": "asked-about" if "?" in user else "demonstrated",
                    "confidence": confidence,
                    "span_start": 0,
                    "span_end": min(len(user), 80),
                }
            ]
        }
