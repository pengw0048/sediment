"""Pytest fixtures."""

from collections.abc import Iterator

import pytest

from pke.app import App
from pke.testing import temp_app


@pytest.fixture()
def app() -> Iterator[App]:
    """Temporary Sediment app."""
    with temp_app() as created:
        yield created
