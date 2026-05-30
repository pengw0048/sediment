"""Drop-in file watcher importer."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from pke.adapters.chatgpt_history import import_chatgpt_archive
from pke.adapters.claude_ai_history import import_claude_archive
from pke.evidence.models import EvidenceEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class InboxResult:
    """Result of processing one inbox file."""

    path: Path
    status: str
    imported: int
    error: str | None = None


def import_dropin_file(path: Path) -> list[EvidenceEvent]:
    """Identify and import one drop-in archive."""
    name = path.name.lower()
    if "claude" in name:
        events = import_claude_archive(path)
    else:
        events = import_chatgpt_archive(path)
    for event in events:
        event.tags.append("dropin")
    return events


def process_inbox_once(inbox: Path) -> list[InboxResult]:
    """Process current files in an inbox directory."""
    inbox.mkdir(parents=True, exist_ok=True)
    processed = inbox / "processed"
    failed = inbox / "failed"
    processed.mkdir(exist_ok=True)
    failed.mkdir(exist_ok=True)
    results: list[InboxResult] = []
    for path in sorted(inbox.iterdir()):
        if path.is_dir() or path.suffix.lower() not in {".json", ".zip"}:
            continue
        try:
            events = import_dropin_file(path)
        except Exception as exc:
            target = failed / path.name
            shutil.move(str(path), target)
            (failed / f"{path.name}.err.txt").write_text(str(exc), encoding="utf-8")
            results.append(InboxResult(path=target, status="failed", imported=0, error=str(exc)))
        else:
            target = processed / path.name
            shutil.move(str(path), target)
            results.append(InboxResult(path=target, status="processed", imported=len(events)))
    return results
