import importlib.resources
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EntryRecord:
    """Mirrors the ``entries`` table row.

    Args:
        title: Article title.
        url: Source URL (unique).
        file_path: Absolute path to the Markdown file in the vault.
        source_type: One of ``article``, ``youtube``, ``substack``, ``x``.
        created_at: ISO 8601 timestamp.
        updated_at: ISO 8601 timestamp.
        status: Entry status (unread/read/favorite/deleted/to_recommend).
        tags_json: JSON-encoded list of tag name strings.
        id: Database row id; ``None`` before insertion.
    """

    title: str
    url: str
    file_path: str
    source_type: str
    created_at: str
    updated_at: str
    status: str = "unread"
    tags_json: str = "[]"
    id: int | None = None


class VaultIndex:
    """SQLite-backed index for vault entries with FTS5 full-text search.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._run_migrations()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> "VaultIndex":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------

    def _run_migrations(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

        migrations_dir = importlib.resources.files("analecta") / "migrations"
        sql_files = sorted(
            r for r in migrations_dir.iterdir() if r.name.endswith(".sql")
        )

        applied = {
            row[0]
            for row in self._conn.execute("SELECT version FROM schema_migrations")
        }

        for resource in sql_files:
            if resource.name in applied:
                continue
            self._conn.executescript(resource.read_text(encoding="utf-8"))
            self._conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (resource.name, _now()),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_entry(self, entry: EntryRecord) -> int:
        """Insert a new entry and seed its FTS row.

        Args:
            entry: Record to insert (``id`` field is ignored).

        Returns:
            The assigned row id.

        Raises:
            sqlite3.IntegrityError: If ``url`` already exists.
        """
        cur = self._conn.execute(
            """
            INSERT INTO entries
                (title, url, file_path, source_type,
                 created_at, updated_at, status, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.title, entry.url, entry.file_path, entry.source_type,
                entry.created_at, entry.updated_at, entry.status, entry.tags_json,
            ),
        )
        entry_id = cur.lastrowid
        self._conn.execute(
            "INSERT INTO entries_fts (rowid, title, content) VALUES (?, ?, ?)",
            (entry_id, entry.title, ""),
        )
        self._conn.commit()
        return entry_id

    def get_entry(self, entry_id: int) -> EntryRecord | None:
        """Fetch a single entry by id.

        Args:
            entry_id: Row id.

        Returns:
            ``EntryRecord`` or ``None`` if not found.
        """
        row = self._conn.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def list_entries(self, status: str | None = None) -> list[EntryRecord]:
        """List entries ordered by creation date descending.

        Args:
            status: Optional status filter.

        Returns:
            List of matching ``EntryRecord`` objects.
        """
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM entries WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM entries ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def update_status(self, entry_id: int, status: str) -> None:
        """Update an entry's status field.

        Args:
            entry_id: Target row id.
            status: New status value.
        """
        self._conn.execute(
            "UPDATE entries SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), entry_id),
        )
        self._conn.commit()

    def update_tags(self, entry_id: int, tags: list[str]) -> None:
        """Replace an entry's tags and keep the tags/entry_tags tables in sync.

        Args:
            entry_id: Target row id.
            tags: New list of tag name strings.
        """
        self._conn.execute(
            "UPDATE entries SET tags_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(tags, ensure_ascii=False), _now(), entry_id),
        )
        self._conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
        for name in tags:
            self._conn.execute(
                "INSERT OR IGNORE INTO tags (name, count) VALUES (?, 0)", (name,)
            )
            self._conn.execute(
                """
                INSERT INTO entry_tags (entry_id, tag_id)
                SELECT ?, id FROM tags WHERE name = ?
                """,
                (entry_id, name),
            )
        self._conn.execute(
            "UPDATE tags SET count = ("
            "  SELECT COUNT(*) FROM entry_tags WHERE tag_id = tags.id"
            ")"
        )
        self._conn.commit()

    def soft_delete(self, entry_id: int) -> None:
        """Mark an entry as deleted without removing it from the database.

        Args:
            entry_id: Target row id.
        """
        self.update_status(entry_id, "deleted")

    def update_fts_content(self, entry_id: int, title: str, content: str) -> None:
        """Replace the FTS5 row for an entry with updated title and body.

        Called by M4 after Markdown conversion is complete.

        Args:
            entry_id: Target row id.
            title: Article title.
            content: Plain-text article body for full-text indexing.
        """
        self._conn.execute("DELETE FROM entries_fts WHERE rowid = ?", (entry_id,))
        self._conn.execute(
            "INSERT INTO entries_fts (rowid, title, content) VALUES (?, ?, ?)",
            (entry_id, title, content),
        )
        self._conn.commit()

    def get_entry_ids_by_tag(self, tag: str) -> list[int]:
        """Return IDs of all entries tagged with *tag*.

        Args:
            tag: Tag name to look up.

        Returns:
            List of entry IDs. Empty if the tag does not exist.
        """
        rows = self._conn.execute(
            """
            SELECT et.entry_id
            FROM entry_tags et
            JOIN tags t ON et.tag_id = t.id
            WHERE t.name = ?
            """,
            (tag,),
        ).fetchall()
        return [row[0] for row in rows]

    def search(self, query: str) -> list[EntryRecord]:
        """Full-text search across title and content using FTS5.

        Args:
            query: FTS5 query string.

        Returns:
            Matching entries ordered by relevance (BM25).

        Raises:
            sqlite3.OperationalError: If ``query`` is not valid FTS5 syntax.
        """
        rows = self._conn.execute(
            """
            SELECT e.* FROM entries e
            JOIN entries_fts fts ON fts.rowid = e.id
            WHERE entries_fts MATCH ?
            ORDER BY rank
            """,
            (query,),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _row_to_entry(row: sqlite3.Row) -> EntryRecord:
    return EntryRecord(
        id=row["id"],
        title=row["title"],
        url=row["url"],
        file_path=row["file_path"],
        source_type=row["source_type"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        status=row["status"],
        tags_json=row["tags_json"],
    )
