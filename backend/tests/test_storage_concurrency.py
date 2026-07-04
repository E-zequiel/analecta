"""Concurrency regression test for VaultIndex's shared sqlite3 connection.

VaultIndex is a singleton wrapping a single sqlite3.Connection, called from
many request-handling threads via asyncio.to_thread. A connection has exactly
one transaction context, so an unsynchronized reader can observe a writer's
uncommitted intermediate state (e.g. index_backlinks' DELETE before its
matching INSERTs commit). This test reproduces that against real threads.
"""

import threading
from pathlib import Path

from analecta.storage.index import EntryRecord, VaultIndex

_ITERATIONS = 300
_READER_COUNT = 4


def test_concurrent_reindex_never_shows_empty_backlinks(tmp_path: Path) -> None:
    vault = tmp_path / "vault" / "pages"
    vault.mkdir(parents=True)

    db = VaultIndex(tmp_path / "vault" / "analecta.db")
    target_id = db.add_entry(
        EntryRecord(
            title="Target",
            url="https://example.com/target",
            file_path=str(vault / "target.md"),
            source_type="article",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )
    src_file = vault / "source.md"
    src_file.write_text("See [[Target]] for details.\n", encoding="utf-8")
    src_id = db.add_entry(
        EntryRecord(
            title="Source",
            url="https://example.com/source",
            file_path=str(src_file),
            source_type="article",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )
    db.index_backlinks(src_id)
    assert len(db.get_backlinks(target_id)) == 1  # sanity check before the race

    stop = threading.Event()
    saw_empty = threading.Event()
    barrier = threading.Barrier(1 + _READER_COUNT)

    def writer() -> None:
        barrier.wait()
        for _ in range(_ITERATIONS):
            if stop.is_set():
                return
            db.index_backlinks(src_id)

    def reader() -> None:
        barrier.wait()
        for _ in range(_ITERATIONS):
            if stop.is_set():
                return
            if len(db.get_backlinks(target_id)) == 0:
                saw_empty.set()
                stop.set()
                return

    threads = [threading.Thread(target=writer)]
    threads += [threading.Thread(target=reader) for _ in range(_READER_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not saw_empty.is_set(), (
        "a reader observed zero backlinks for a target the source always "
        "references — the shared-connection transaction-visibility race"
    )
    db.close()
