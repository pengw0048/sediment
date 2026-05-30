"""Reusable test fixtures."""

from __future__ import annotations

from contextlib import contextmanager
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
