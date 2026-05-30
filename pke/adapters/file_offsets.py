"""JSON-backed per-file byte offsets for resumable JSONL tailers.

Stored at ``~/.local/share/pke/tailer_offsets.json`` by default. The
file is keyed by absolute path and tracks two fields per file:

* ``offset`` — the byte position immediately after the last record the
  tailer consumed.
* ``inode`` — the inode number captured when ``offset`` was recorded.
  On the next read we compare the live inode with this value to detect
  log-rotation: if they differ, the file was rotated under us and we
  must start from offset 0 instead of seeking past the new file's end.

The store is process-shared but not thread-safe; callers serialize.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OFFSETS_PATH = Path("~/.local/share/pke/tailer_offsets.json").expanduser()


@dataclass(frozen=True, kw_only=True, slots=True)
class FileOffset:
    """One file's resume state."""

    offset: int
    inode: int


class FileOffsetStore:
    """JSON-backed offset store keyed by absolute file path."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_OFFSETS_PATH

    def get(self, file_path: Path) -> FileOffset | None:
        """Return the stored offset for ``file_path``, or ``None`` if absent."""
        data = self._read()
        record = data.get(str(file_path.resolve()))
        if not isinstance(record, dict):
            return None
        try:
            return FileOffset(offset=int(record["offset"]), inode=int(record["inode"]))
        except (KeyError, TypeError, ValueError):
            return None

    def set(self, file_path: Path, *, offset: int, inode: int) -> None:
        """Persist ``offset`` for ``file_path`` with the live inode."""
        data = self._read()
        data[str(file_path.resolve())] = {"offset": int(offset), "inode": int(inode)}
        self._write(data)

    def resume_offset(self, file_path: Path) -> int:
        """Return the byte offset to start reading from for ``file_path``.

        Compares the stored inode against the live inode and resets to 0
        when they differ (logrotate, file recreated, etc.). When the
        file is missing or the store has no entry, returns 0.
        """
        try:
            live_inode = file_path.stat().st_ino
        except FileNotFoundError:
            return 0
        record = self.get(file_path)
        if record is None or record.inode != live_inode:
            return 0
        return record.offset

    def remember_after_read(self, file_path: Path, *, offset: int) -> None:
        """Persist ``offset`` together with the file's current inode."""
        try:
            inode = file_path.stat().st_ino
        except FileNotFoundError:
            return
        self.set(file_path, offset=offset, inode=inode)

    def _read(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write via tmp + rename so a crash never leaves
        # an empty offsets file behind.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)


__all__ = ["DEFAULT_OFFSETS_PATH", "FileOffset", "FileOffsetStore"]
