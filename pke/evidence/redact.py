"""Local redaction helpers for evidence content."""

from __future__ import annotations

import re

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{16,})"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


def redact_text(text: str) -> str:
    """Replace common secrets and emails with local placeholders."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
