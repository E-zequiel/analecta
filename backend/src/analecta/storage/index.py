import importlib.resources
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ALLOWED_SORT_COLS: frozenset[str] = frozenset({"title", "created_at"})
_ALLOWED_SORT_DIRS: frozenset[str] = frozenset({"asc", "desc"})


@dataclass
class BacklinkRecord:
    """A resolved backlink reference pointing to an entry.

    Args:
        source_id: ID of the entry containing the reference.
        source_title: Title of the source entry.
        heading: Section heading above the reference, or ``None``.
        pre: Text immediately before the reference (up to 60 chars).
        highlight: The matched link text as it appears in source.
        post: Text immediately after the reference (up to 60 chars).
    """

    source_id: int
    source_title: str
    heading: str | None
    pre: str
    highlight: str
    post: str


@dataclass
class GraphNodeRecord:
    """A node in the vault connection graph.

    Args:
        node_id: Prefixed stable identifier — ``entry:{int_id}`` or ``tag:{name}``.
        label: Display label (entry title or ``#tagname``).
        kind: Node kind: ``entry`` or ``tag``.
        source_type: Entry source type (``article``, ``youtube``, etc.) or ``None``.
    """

    node_id: str
    label: str
    kind: str
    source_type: str | None


@dataclass
class GraphEdgeRecord:
    """A weighted directed edge in the vault connection graph.

    Args:
        source: Source node id.
        target: Target node id.
        weight: Number of individual references collapsed into this edge.
    """

    source: str
    target: str
    weight: int


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
    flags_json: str = "[]"
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

    def __enter__(self) -> VaultIndex:
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
            (r for r in migrations_dir.iterdir() if r.name.endswith(".sql")),
            key=lambda r: r.name,
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
                 created_at, updated_at, status, tags_json, flags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.title,
                entry.url,
                entry.file_path,
                entry.source_type,
                entry.created_at,
                entry.updated_at,
                entry.status,
                entry.tags_json,
                entry.flags_json,
            ),
        )
        entry_id = cur.lastrowid
        assert entry_id is not None  # guaranteed after INSERT
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

    def list_entries(
        self,
        status: str | None = None,
        flag: str | None = None,
        exclude_flag: str | None = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
    ) -> list[EntryRecord]:
        """List entries with optional filters and sort control.

        Args:
            status: Optional status filter.
            flag: Optional flag filter (bookmark / gem / archive); matches entries
                whose flags_json array contains this value.
            exclude_flag: Optional flag exclusion; omits entries whose flags_json
                array contains this value (e.g. ``"archive"`` for library view).
            sort_by: Column to sort by — ``title`` or ``created_at``.
                Defaults to ``created_at``.
            sort_dir: Sort direction — ``asc`` or ``desc``. Defaults to ``desc``.

        Returns:
            List of matching ``EntryRecord`` objects.
        """
        if sort_by not in _ALLOWED_SORT_COLS:
            sort_by = "created_at"
        if sort_dir not in _ALLOWED_SORT_DIRS:
            sort_dir = "desc"
        conditions: list[str] = []
        params: list[str] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if flag is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(flags_json) WHERE value = ?)"
            )
            params.append(flag)
        if exclude_flag is not None:
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM json_each(flags_json) WHERE value = ?)"
            )
            params.append(exclude_flag)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM entries {where} ORDER BY {sort_by} {sort_dir.upper()}",
            params,
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get_counts(self) -> dict[str, int]:
        """Return entry counts for all dashboard sections in one aggregated query.

        Returns:
            Dict with keys ``library``, ``unread``, ``read``, ``bookmark``,
            ``gem``, ``archive`` mapping to their current entry counts.
        """
        row = self._conn.execute(
            """
            SELECT
                SUM(CASE WHEN NOT EXISTS (
                    SELECT 1 FROM json_each(flags_json) WHERE value = 'archive'
                ) THEN 1 ELSE 0 END) AS library,
                SUM(CASE WHEN status = 'unread' AND NOT EXISTS (
                    SELECT 1 FROM json_each(flags_json) WHERE value = 'archive'
                ) THEN 1 ELSE 0 END) AS unread,
                SUM(CASE WHEN status = 'read' AND NOT EXISTS (
                    SELECT 1 FROM json_each(flags_json) WHERE value = 'archive'
                ) THEN 1 ELSE 0 END) AS read_count,
                SUM(CASE WHEN EXISTS (
                    SELECT 1 FROM json_each(flags_json) WHERE value = 'bookmark'
                ) AND NOT EXISTS (
                    SELECT 1 FROM json_each(flags_json) WHERE value = 'archive'
                ) THEN 1 ELSE 0 END) AS bookmark,
                SUM(CASE WHEN EXISTS (
                    SELECT 1 FROM json_each(flags_json) WHERE value = 'gem'
                ) AND NOT EXISTS (
                    SELECT 1 FROM json_each(flags_json) WHERE value = 'archive'
                ) THEN 1 ELSE 0 END) AS gem,
                SUM(CASE WHEN EXISTS (
                    SELECT 1 FROM json_each(flags_json) WHERE value = 'archive'
                ) THEN 1 ELSE 0 END) AS archive
            FROM entries
            """
        ).fetchone()
        return {
            "library": row["library"] or 0,
            "unread": row["unread"] or 0,
            "read": row["read_count"] or 0,
            "bookmark": row["bookmark"] or 0,
            "gem": row["gem"] or 0,
            "archive": row["archive"] or 0,
        }

    def get_metrics(self) -> dict[str, int]:
        """Return read-activity metrics used by the Collecta dashboard.

        Counts entries whose ``read_at`` timestamp falls within the current
        calendar week (Mon-Sun), month, and year respectively.

        Returns:
            Dict with keys ``reads_week``, ``reads_month``, ``reads_year``.
        """
        from datetime import timedelta

        now = datetime.now(tz=UTC)
        week_start = (now - timedelta(days=now.weekday())).date().isoformat()
        month_start = now.strftime("%Y-%m-01")
        year_start = now.strftime("%Y-01-01")
        row = self._conn.execute(
            """
            SELECT
                SUM(CASE WHEN read_at >= ? THEN 1 ELSE 0 END),
                SUM(CASE WHEN read_at >= ? THEN 1 ELSE 0 END),
                SUM(CASE WHEN read_at >= ? THEN 1 ELSE 0 END)
            FROM entries
            WHERE read_at IS NOT NULL
            """,
            (week_start, month_start, year_start),
        ).fetchone()
        return {
            "reads_week": row[0] or 0,
            "reads_month": row[1] or 0,
            "reads_year": row[2] or 0,
        }

    def update_status(self, entry_id: int, status: str) -> None:
        """Update an entry's status field.

        Sets ``read_at`` to the current timestamp when *status* is ``'read'``
        so that Collecta read-activity metrics remain accurate.

        Args:
            entry_id: Target row id.
            status: New status value.
        """
        if status == "read":
            now = _now()
            self._conn.execute(
                "UPDATE entries SET status = ?, updated_at = ?, read_at = ?"
                " WHERE id = ?",
                (status, now, now, entry_id),
            )
        else:
            self._conn.execute(
                "UPDATE entries SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), entry_id),
            )
        self._conn.commit()

    def update_flags(self, entry_id: int, flags: list[str]) -> None:
        """Replace an entry's flags list.

        Args:
            entry_id: Target row id.
            flags: New list of flag strings (e.g. ``["bookmark"]``).
        """
        self._conn.execute(
            "UPDATE entries SET flags_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(flags, ensure_ascii=False), _now(), entry_id),
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

    def hard_delete(self, entry_id: int) -> None:
        """Permanently remove an entry and all its associations from the database.

        Removes entry_tags rows, recalculates tag counts, removes the FTS index
        row, and deletes the entry row. Does not touch the vault file — caller
        is responsible for file removal.

        Args:
            entry_id: Target row id.
        """
        self._conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
        self._conn.execute(
            "UPDATE tags SET count = ("
            "  SELECT COUNT(*) FROM entry_tags WHERE tag_id = tags.id"
            ")"
        )
        self._conn.execute("DELETE FROM entries_fts WHERE rowid = ?", (entry_id,))
        self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self._conn.commit()

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

    def create_tag(self, name: str) -> None:
        """Create a standalone tag with no entries.

        Args:
            name: Tag name to create. Does nothing if it already exists.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO tags (name, count) VALUES (?, 0)", (name,)
        )
        self._conn.commit()

    def rename_tag(self, old_name: str, new_name: str) -> None:
        """Rename a tag globally.

        Updates the tags table and re-serialises ``tags_json`` in all affected entries.

        Args:
            old_name: Current tag name.
            new_name: Replacement tag name.

        Raises:
            ValueError: If a tag named *new_name* already exists.
        """
        tag_row = self._conn.execute(
            "SELECT id FROM tags WHERE name = ?", (old_name,)
        ).fetchone()
        if tag_row is None:
            return
        tag_id = tag_row["id"]
        if self._conn.execute(
            "SELECT id FROM tags WHERE name = ?", (new_name,)
        ).fetchone():
            raise ValueError(f"Tag '{new_name}' already exists")
        entry_ids = [
            row[0]
            for row in self._conn.execute(
                "SELECT entry_id FROM entry_tags WHERE tag_id = ?", (tag_id,)
            ).fetchall()
        ]
        self._conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
        now = _now()
        for eid in entry_ids:
            row = self._conn.execute(
                "SELECT tags_json FROM entries WHERE id = ?", (eid,)
            ).fetchone()
            if row:
                tags = [
                    new_name if t == old_name else t
                    for t in json.loads(row["tags_json"])
                ]
                self._conn.execute(
                    "UPDATE entries SET tags_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(tags, ensure_ascii=False), now, eid),
                )
        self._conn.commit()

    def delete_tag(self, name: str) -> None:
        """Delete a tag globally.

        Removes it from the tags table, entry_tags, and re-serialises ``tags_json``
        in all affected entries.

        Args:
            name: Tag name to delete. Does nothing if it does not exist.
        """
        tag_row = self._conn.execute(
            "SELECT id FROM tags WHERE name = ?", (name,)
        ).fetchone()
        if tag_row is None:
            return
        tag_id = tag_row["id"]
        entry_ids = [
            row[0]
            for row in self._conn.execute(
                "SELECT entry_id FROM entry_tags WHERE tag_id = ?", (tag_id,)
            ).fetchall()
        ]
        now = _now()
        for eid in entry_ids:
            row = self._conn.execute(
                "SELECT tags_json FROM entries WHERE id = ?", (eid,)
            ).fetchone()
            if row:
                tags = [t for t in json.loads(row["tags_json"]) if t != name]
                self._conn.execute(
                    "UPDATE entries SET tags_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(tags, ensure_ascii=False), now, eid),
                )
        self._conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (tag_id,))
        self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self._conn.commit()

    def index_backlinks(self, source_id: int) -> None:
        """Re-index all outgoing backlink refs for *source_id*.

        Reads the entry's Markdown file, parses ``[[wikilinks]]`` and
        ``#hashtags``, clears any previously indexed refs for this source,
        and inserts fresh rows into ``backlink_refs``.

        Args:
            source_id: ID of the entry whose file to re-parse.
        """
        from analecta.markdown.backlinks import parse_refs

        entry = self.get_entry(source_id)
        if entry is None:
            return
        file_path = Path(entry.file_path)
        if not file_path.exists():
            return

        markdown = file_path.read_text(encoding="utf-8")
        refs = parse_refs(markdown)

        self._conn.execute(
            "DELETE FROM backlink_refs WHERE source_id = ?", (source_id,)
        )
        for ref in refs:
            self._conn.execute(
                """
                INSERT INTO backlink_refs
                    (source_id, target_text, is_hashtag, heading, pre, highlight, post)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    ref.target_text,
                    1 if ref.is_hashtag else 0,
                    ref.heading,
                    ref.pre,
                    ref.highlight,
                    ref.post,
                ),
            )
        self._conn.commit()

    def get_backlinks(self, target_id: int) -> list[BacklinkRecord]:
        """Return all entries that link to *target_id*.

        Resolves ``backlink_refs`` against the current ``entries`` table.
        Wikilinks are matched by lowercased title; hashtags by normalized
        (snake_case) title.

        Args:
            target_id: ID of the entry to query backlinks for.

        Returns:
            List of :class:`BacklinkRecord` objects ordered by source title
            then document position.
        """
        from analecta.markdown.hashtags import normalize_tag

        target_row = self._conn.execute(
            "SELECT title FROM entries WHERE id = ?", (target_id,)
        ).fetchone()
        if target_row is None:
            return []

        title_lower = target_row["title"].lower()
        title_slug = normalize_tag(target_row["title"])

        rows = self._conn.execute(
            """
            SELECT br.source_id, e.title, br.heading, br.pre, br.highlight, br.post
            FROM backlink_refs br
            JOIN entries e ON e.id = br.source_id
            WHERE e.id != ?
              AND (
                (br.is_hashtag = 0 AND br.target_text = ?)
                OR (br.is_hashtag = 1 AND br.target_text = ?)
              )
            ORDER BY e.title ASC, br.source_id ASC, br.id ASC
            """,
            (target_id, title_lower, title_slug),
        ).fetchall()

        return [
            BacklinkRecord(
                source_id=row[0],
                source_title=row[1],
                heading=row[2],
                pre=row[3],
                highlight=row[4],
                post=row[5],
            )
            for row in rows
        ]

    def list_tags(self) -> list[tuple[str, int]]:
        """Return all tags sorted by entry count descending.

        Returns:
            List of ``(name, count)`` tuples.
        """
        rows = self._conn.execute(
            "SELECT name, count FROM tags ORDER BY count DESC, name ASC"
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def get_graph(
        self,
    ) -> tuple[list[GraphNodeRecord], list[GraphEdgeRecord]]:
        """Return all connected nodes and weighted edges for the vault graph.

        Resolves ``backlink_refs`` against the current ``entries`` table using
        the same title-matching rules as :meth:`get_backlinks`. Wikilinks that
        do not resolve to an existing entry are skipped. Unresolved hashtags
        produce virtual tag nodes (``tag:{name}``). Multiple occurrences of the
        same source→target pair are collapsed into a single weighted edge.
        Entries with no connections (isolated nodes) are excluded.

        Returns:
            Tuple of ``(nodes, edges)``.  Nodes include both ``entry:`` and
            ``tag:`` kinds.  Edges are directed but the frontend may treat them
            as undirected for layout purposes.
        """
        from analecta.markdown.hashtags import normalize_tag

        entry_rows = self._conn.execute(
            "SELECT id, title, source_type FROM entries"
        ).fetchall()
        entries: dict[int, tuple[str, str]] = {
            row[0]: (row[1], row[2]) for row in entry_rows
        }

        lower_title_to_id: dict[str, int] = {
            title.lower(): eid for eid, (title, _) in entries.items()
        }
        slug_to_id: dict[str, int] = {
            normalize_tag(title): eid for eid, (title, _) in entries.items()
        }

        refs = self._conn.execute(
            "SELECT source_id, target_text, is_hashtag FROM backlink_refs"
        ).fetchall()

        edge_weights: dict[tuple[str, str], int] = {}
        virtual_tags: set[str] = set()

        for ref in refs:
            source_id: int = ref[0]
            target_text: str = ref[1]
            is_hashtag: bool = bool(ref[2])

            if source_id not in entries:
                continue

            source_node = f"entry:{source_id}"

            if not is_hashtag:
                target_id = lower_title_to_id.get(target_text)
                if target_id is None or target_id == source_id:
                    continue
                target_node = f"entry:{target_id}"
            else:
                target_id = slug_to_id.get(target_text)
                if target_id is not None and target_id != source_id:
                    target_node = f"entry:{target_id}"
                else:
                    target_node = f"tag:{target_text}"
                    virtual_tags.add(target_text)

            key = (source_node, target_node)
            edge_weights[key] = edge_weights.get(key, 0) + 1

        connected_entry_ids: set[int] = set()
        for s, t in edge_weights:
            if s.startswith("entry:"):
                connected_entry_ids.add(int(s[6:]))
            if t.startswith("entry:"):
                connected_entry_ids.add(int(t[6:]))

        nodes: list[GraphNodeRecord] = [
            GraphNodeRecord(
                node_id=f"entry:{eid}",
                label=entries[eid][0],
                kind="entry",
                source_type=entries[eid][1],
            )
            for eid in connected_entry_ids
        ]
        nodes += [
            GraphNodeRecord(
                node_id=f"tag:{name}",
                label=f"#{name}",
                kind="tag",
                source_type=None,
            )
            for name in virtual_tags
        ]

        edges: list[GraphEdgeRecord] = [
            GraphEdgeRecord(source=s, target=t, weight=w)
            for (s, t), w in edge_weights.items()
        ]

        return nodes, edges

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
    return datetime.now(tz=UTC).isoformat()


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
        flags_json=row["flags_json"],
    )
