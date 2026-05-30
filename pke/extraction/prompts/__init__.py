"""Jinja2 prompt template loader for the extraction layer.

Templates live next to this module. They are stored as `.j2` files and rendered
at call sites via :func:`render`. Variables in templates use Jinja2 expression
syntax; referencing an undefined variable raises :class:`StrictUndefined` so we
fail loud instead of silently emitting a broken prompt.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_TEMPLATE_DIR = Path(__file__).parent

_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=False,
    keep_trailing_newline=True,
    undefined=StrictUndefined,
)


def render(name: str, /, **context: object) -> str:
    """Render a prompt template by file name.

    Args:
        name: Template file name, e.g. ``"extract_skills.system.j2"``.
        **context: Variables passed to Jinja2.
    """
    return _env.get_template(name).render(**context)
