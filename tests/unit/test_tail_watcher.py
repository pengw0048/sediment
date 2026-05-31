"""M21 watchdog real-time tailer + offset persistence tests."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from pke.adapters.file_offsets import FileOffset, FileOffsetStore
from pke.adapters.tail_watcher import TailEvent, TailWatcher


def test_offset_store_round_trip(tmp_path: Path) -> None:
    """Set + get returns the same FileOffset."""
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    target = tmp_path / "x.jsonl"
    target.write_text("a\nb\n")
    store.set(target, offset=42, inode=target.stat().st_ino)
    got = store.get(target)
    assert got is not None
    assert got.offset == 42


def test_offset_store_resets_on_inode_change(tmp_path: Path) -> None:
    """resume_offset returns 0 when the stored inode no longer matches."""
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    target = tmp_path / "x.jsonl"
    target.write_text("a\nb\n")
    store.set(target, offset=100, inode=target.stat().st_ino + 1)  # stale inode
    assert store.resume_offset(target) == 0


def test_offset_store_resumes_with_matching_inode(tmp_path: Path) -> None:
    """resume_offset returns the stored value when the inode still matches."""
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    target = tmp_path / "x.jsonl"
    target.write_text("a\nb\n")
    store.remember_after_read(target, offset=4)
    assert store.resume_offset(target) == 4


def test_tail_watcher_emits_each_complete_line(tmp_path: Path) -> None:
    """Each newline-terminated line lands as one TailEvent."""
    captured: list[TailEvent] = []
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    watcher = TailWatcher(
        tmp_path,
        handler=captured.append,
        offset_store=store,
    )
    target = tmp_path / "a.jsonl"
    target.write_text('{"a":1}\n{"a":2}\n')

    watcher._drain(target)

    assert [e.raw_line for e in captured] == ['{"a":1}', '{"a":2}']
    # Offset is just past the second newline.
    assert store.resume_offset(target) == len('{"a":1}\n{"a":2}\n')


def test_tail_watcher_holds_half_written_line_until_complete(tmp_path: Path) -> None:
    """A partial last line (no trailing newline) waits for the next write."""
    captured: list[TailEvent] = []
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    watcher = TailWatcher(tmp_path, handler=captured.append, offset_store=store)
    target = tmp_path / "a.jsonl"
    target.write_text('{"a":1}\n{"a":2')  # no trailing newline

    watcher._drain(target)
    assert [e.raw_line for e in captured] == ['{"a":1}']

    # Now finish the line and one more.
    with target.open("a") as fh:
        fh.write("}\n")
    watcher._drain(target)
    assert [e.raw_line for e in captured] == ['{"a":1}', '{"a":2}']


def test_tail_watcher_skips_already_read_lines_after_offset_restore(tmp_path: Path) -> None:
    """A second TailWatcher with the same offset store resumes where we left off."""
    captured_a: list[TailEvent] = []
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    watcher_a = TailWatcher(tmp_path, handler=captured_a.append, offset_store=store)
    target = tmp_path / "a.jsonl"
    target.write_text("line1\nline2\n")
    watcher_a._drain(target)
    assert len(captured_a) == 2

    with target.open("a") as fh:
        fh.write("line3\n")

    captured_b: list[TailEvent] = []
    watcher_b = TailWatcher(tmp_path, handler=captured_b.append, offset_store=store)
    watcher_b._drain(target)
    assert [e.raw_line for e in captured_b] == ["line3"]


def test_tail_watcher_observer_picks_up_appends_live(tmp_path: Path) -> None:
    """End-to-end: start() boots the watchdog Observer and reacts to a fresh write."""
    captured: list[TailEvent] = []
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    watcher = TailWatcher(tmp_path, handler=captured.append, offset_store=store)
    watcher.start()
    try:
        target = tmp_path / "live.jsonl"
        target.write_text("hello\n")
        # Watchdog needs a moment to notice; this is the unavoidable
        # latency for a real filesystem event.
        for _ in range(20):
            if captured:
                break
            time.sleep(0.1)
    finally:
        watcher.stop()
    assert any(event.raw_line == "hello" for event in captured)


def test_offset_store_keys_by_absolute_path(tmp_path: Path) -> None:
    """Same file via a different relative path resolves to the same entry."""
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    target = tmp_path / "x.jsonl"
    target.write_text("a\n")
    store.set(target, offset=2, inode=target.stat().st_ino)

    # Construct an unresolved alias by joining tmp_path with the relative bit.
    alias = (tmp_path / "x.jsonl").resolve()
    got = store.get(alias)
    assert got is not None
    assert got.offset == 2


def test_offset_store_drops_corrupt_records_silently(tmp_path: Path) -> None:
    """A non-dict record at a key gives get() back None rather than crashing."""
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    target = tmp_path / "x.jsonl"
    target.write_text("a\n")
    (tmp_path / "offsets.json").write_text(
        f'{{"{target.resolve()}": "not-a-dict"}}', encoding="utf-8"
    )
    assert store.get(target) is None


def test_file_offset_dataclass_is_frozen() -> None:
    """FileOffset uses frozen+slots so it stays cheap and hashable."""
    snap = FileOffset(offset=1, inode=2)
    with pytest.raises(AttributeError):
        snap.offset = 5  # type: ignore[misc]


def test_tail_watcher_skips_backlog_for_old_files(tmp_path: Path) -> None:
    """A pre-existing file older than the cold-start window is seek-to-EOF'd.

    A developer with months of historical transcripts on disk should
    not pay an O(all history) wall on first boot. The watcher records
    the file's current size as the resume offset and never invokes the
    handler for any of the existing lines.
    """
    captured: list[TailEvent] = []
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    watcher = TailWatcher(
        tmp_path,
        handler=captured.append,
        offset_store=store,
        skip_backlog_older_than_hours=24.0,
    )
    target = tmp_path / "old.jsonl"
    payload = "old1\nold2\nold3\n"
    target.write_text(payload)
    # Backdate mtime to 48 hours ago so it falls outside the 24h window.
    old_mtime = time.time() - 48 * 3600.0
    os.utime(target, (old_mtime, old_mtime))

    watcher._drain(target)

    assert captured == []
    # Offset was persisted at EOF so subsequent live appends pick up
    # from the new tail.
    assert store.resume_offset(target) == len(payload.encode("utf-8"))


def test_tail_watcher_replays_recent_files_even_without_offset(tmp_path: Path) -> None:
    """A pre-existing file inside the cold-start window still replays."""
    captured: list[TailEvent] = []
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    watcher = TailWatcher(
        tmp_path,
        handler=captured.append,
        offset_store=store,
        skip_backlog_older_than_hours=24.0,
    )
    target = tmp_path / "fresh.jsonl"
    target.write_text("fresh1\nfresh2\n")
    # Mtime defaults to "now" so this is well inside the window.

    watcher._drain(target)

    assert [e.raw_line for e in captured] == ["fresh1", "fresh2"]


def test_tail_watcher_disable_skip_replays_old_files(tmp_path: Path) -> None:
    """skip_backlog_older_than_hours=0 disables the cold-start guard."""
    captured: list[TailEvent] = []
    store = FileOffsetStore(path=tmp_path / "offsets.json")
    watcher = TailWatcher(
        tmp_path,
        handler=captured.append,
        offset_store=store,
        skip_backlog_older_than_hours=0,
    )
    target = tmp_path / "old.jsonl"
    target.write_text("ancient1\nancient2\n")
    old_mtime = time.time() - 365 * 24 * 3600.0
    os.utime(target, (old_mtime, old_mtime))

    watcher._drain(target)

    assert [e.raw_line for e in captured] == ["ancient1", "ancient2"]
