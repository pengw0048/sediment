"""Advisory lock helpers for maintenance jobs."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Acquire a best-effort single-process lock file."""
    if path.exists():
        raise RuntimeError(f"lock already exists: {path}")
    path.write_text("locked", encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
