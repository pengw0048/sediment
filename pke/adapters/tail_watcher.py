r"""watchdog Observer that tails JSONL files for real-time evidence ingestion.

Used by the Claude Code transcript adapter and the inbox adapter as the
real-time counterpart to the one-shot ``parse_transcript_file`` / inbox
scan. Wraps :class:`watchdog.observers.Observer` so the daemon can
register a directory once and receive a callback for every new line
appended to any matching file inside it, while resume offsets are
persisted to ``FileOffsetStore`` so a daemon restart picks up where it
left off rather than re-reading the whole transcript.

Single-line JSON only — the tailer reads up to the last complete ``\n``
and stops there, leaving a half-written line for the next event.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pke.adapters.file_offsets import FileOffsetStore


@dataclass(frozen=True, kw_only=True, slots=True)
class TailEvent:
    """One raw JSONL line read by the tailer."""

    path: Path
    line_number: int
    raw_line: str


LineHandler = Callable[[TailEvent], None]


class TailWatcher:
    """Watch a directory for appended JSONL lines and dispatch them.

    Starts a watchdog ``Observer`` on first :meth:`start` call; on
    every ``on_modified`` / ``on_created`` event it reads the file from
    the resumed offset to the latest complete newline and calls
    ``handler`` once per new line. The offset store is updated only
    after the handler returns, so a crashing handler causes the line
    to be replayed on the next start rather than silently lost.
    """

    def __init__(
        self,
        directory: Path,
        *,
        handler: LineHandler,
        offset_store: FileOffsetStore | None = None,
        glob: str = "*.jsonl",
    ) -> None:
        self._directory = directory
        self._handler = handler
        self._offset_store = offset_store or FileOffsetStore()
        self._glob = glob
        self._lock = threading.Lock()
        self._observer: Any | None = None

    def start(self) -> None:
        """Bring the watcher online; idempotent."""
        if self._observer is not None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        # Pull in any lines that landed before we started so we don't
        # silently swallow a restart-time backlog.
        for path in sorted(self._directory.glob(self._glob)):
            self._drain(path)
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class _Handler(FileSystemEventHandler):
            def __init__(handler_self, outer: TailWatcher) -> None:
                handler_self._outer = outer

            def on_modified(handler_self, event: Any) -> None:
                if event.is_directory:
                    return
                handler_self._outer._maybe_drain(Path(str(event.src_path)))

            on_created = on_modified

        self._observer = Observer()
        self._observer.schedule(  # type: ignore[no-untyped-call]
            _Handler(self), str(self._directory), recursive=False
        )
        self._observer.start()  # type: ignore[no-untyped-call]

    def stop(self) -> None:
        """Shut down the observer; idempotent."""
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=5.0)
        self._observer = None

    def _maybe_drain(self, path: Path) -> None:
        if not path.match(self._glob):
            return
        self._drain(path)

    def _drain(self, path: Path) -> None:
        with self._lock:
            if not path.exists() or not path.is_file():
                return
            start_at = self._offset_store.resume_offset(path)
            file_size = path.stat().st_size
            if start_at >= file_size:
                return
            with path.open("rb") as fh:
                fh.seek(start_at)
                data = fh.read()
            text = data.decode("utf-8", errors="ignore")
            last_newline = text.rfind("\n")
            if last_newline < 0:
                return
            consumed = text[: last_newline + 1]
            new_offset = start_at + len(consumed.encode("utf-8"))
            line_number_base = self._count_lines_before(path, start_at)
            for offset_in_chunk, line in enumerate(consumed.splitlines()):
                if not line.strip():
                    continue
                self._handler(
                    TailEvent(
                        path=path,
                        line_number=line_number_base + offset_in_chunk,
                        raw_line=line,
                    )
                )
            self._offset_store.remember_after_read(path, offset=new_offset)

    @staticmethod
    def _count_lines_before(path: Path, byte_offset: int) -> int:
        """Count newlines in ``path`` strictly before ``byte_offset`` for line numbering."""
        if byte_offset <= 0:
            return 0
        with path.open("rb") as fh:
            preamble = fh.read(byte_offset)
        return preamble.count(b"\n")


__all__ = ["LineHandler", "TailEvent", "TailWatcher"]
