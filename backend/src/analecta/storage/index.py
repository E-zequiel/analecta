from __future__ import annotations

import functools
import importlib.resources
import json
import re as _re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Concatenate

from analecta.markdown.hashtags import title_to_hashtag_key

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
class OutgoingLinkRecord:
    """A resolved outgoing wikilink/hashtag reference to another entry.

    Args:
        target_id: ID of the entry being linked to.
        target_title: Title of the entry being linked to.
        heading: Section heading above the reference, or ``None``.
        pre: Text immediately before the reference (up to 60 chars).
        highlight: The matched link text as it appears in source.
        post: Text immediately after the reference (up to 60 chars).
    """

    target_id: int
    target_title: str
    heading: str | None
    pre: str
    highlight: str
    post: str


@dataclass
class GraphNodeRecord:
    """A node in the vault connection graph.

    Args:
        node_id: Prefixed stable identifier — ``entry:{int_id}`` or
            ``tag:{normalized}`` (``casefold`` identity, shared by a
            structural tag and a content hashtag of the same name).
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


@dataclass
class HashtagConnectionGroup:
    """Other entries sharing a common content hashtag with the source entry.

    Args:
        hashtag: Normalized hashtag text (e.g. ``python``).
        entries: All other entries whose ``backlink_refs`` contain this hashtag.
    """

    hashtag: str
    entries: list[EntryRecord]


def _synchronized[**P, R](
    fn: Callable[Concatenate[VaultIndex, P], R],
) -> Callable[Concatenate[VaultIndex, P], R]:
    """Serialize a VaultIndex method against the shared sqlite3.Connection.

    A single connection is shared across every request-handling thread, and
    a connection has exactly one transaction context — so without this, a
    reader thread can observe another thread's uncommitted multi-statement
    write (e.g. ``index_backlinks``' ``DELETE`` before its matching
    ``INSERT``s commit). Held for the whole method, not per-statement, so
    multi-statement writes stay atomic from a concurrent reader's
    perspective. Uses ``RLock`` because several methods call other
    decorated methods on ``self`` (e.g. ``add_link`` calls ``get_entry``).

    Args:
        fn: Method to wrap.

    Returns:
        Wrapped method that acquires ``self._lock`` before calling *fn*.
    """

    @functools.wraps(fn)
    def wrapper(self: VaultIndex, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:  # pyright: ignore[reportPrivateUsage] — decorator is VaultIndex's own synchronization helper
            return fn(self, *args, **kwargs)

    return wrapper


class InvalidTagNameError(ValueError):
    """Raised when a tag name can't parse as a live inline ``#hashtag``.

    Every tag created or renamed through the UI must round-trip through
    :func:`~analecta.markdown.backlinks.is_valid_hashtag_literal`, so it can
    always be written as a literal ``#hashtag`` in an entry body. Raised by
    :meth:`VaultIndex.create_tag` and :meth:`VaultIndex.rename_tag`.
    """


class VaultIndex:
    """SQLite-backed index for vault entries with FTS5 full-text search.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
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

        # One-time Python migration: populate backlink_refs for entries that
        # existed before 007_backlinks.sql created the table.
        _py_bootstrap = "py:008_backlinks_bootstrap"
        if _py_bootstrap not in applied:
            self._bootstrap_backlinks()
            self._conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (_py_bootstrap, _now()),
            )
            self._conn.commit()

        # One-time Python migration: merge pre-existing case-duplicate tags
        # (e.g. "Python" and "python" as separate rows) and backfill
        # tags.normalized, added by 008_tags_normalized.sql.
        _py_tag_bootstrap = "py:008_tag_normalization_backfill"
        if _py_tag_bootstrap not in applied:
            self._bootstrap_tag_normalization()
            self._conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (_py_tag_bootstrap, _now()),
            )
            self._conn.commit()

        # Runs every startup (not gated by schema_migrations) — catches any
        # entry whose Markdown file was edited outside the app since the
        # last reconcile.
        self.reconcile_stale_entries()

    def _bootstrap_backlinks(self) -> None:
        """Populate backlink_refs for entries that have never been indexed.

        Entries created before migration 007_backlinks.sql have no rows in
        backlink_refs.  This one-time method calls index_backlinks for each
        such entry so that hashtag and wikilink connections become available.
        """
        rows = self._conn.execute(
            """
            SELECT e.id FROM entries e
            LEFT JOIN backlink_refs br ON br.source_id = e.id
            WHERE br.source_id IS NULL
            """
        ).fetchall()
        for row in rows:
            self.index_backlinks(row[0])

    def _bootstrap_tag_normalization(self) -> None:
        """Merge case-duplicate tag rows and backfill ``tags.normalized``.

        Before 008_tags_normalized.sql, ``tags.name`` uniqueness was
        exact-string, so "Python" and "python" could exist as two
        separate rows with separate ``entry_tags`` memberships. Groups
        existing rows by ``name.casefold()``; for any group with more
        than one row, keeps the row with the most real ``entry_tags``
        memberships (computed live via ``COUNT(*)``, tie-broken by
        lowest id — never the old, unmaintained ``count`` column) as
        canonical, reassigns every duplicate's ``entry_tags`` rows and
        ``tags_json`` entries to it, and deletes the duplicates. Then
        backfills ``normalized`` on every surviving row and creates the
        unique index that prevents new case-duplicates from here on —
        deliberately built *after* the merge, since creating it first
        would fail on any pre-existing duplicate.
        """
        rows = self._conn.execute("SELECT id, name FROM tags").fetchall()
        groups: dict[str, list[tuple[int, str]]] = {}
        for tag_id, name in rows:
            groups.setdefault(name.casefold(), []).append((tag_id, name))

        now = _now()
        for key, variants in groups.items():
            if len(variants) > 1:
                counts = [
                    (
                        self._conn.execute(
                            "SELECT COUNT(*) FROM entry_tags WHERE tag_id = ?",
                            (tid,),
                        ).fetchone()[0],
                        tid,
                        name,
                    )
                    for tid, name in variants
                ]
                counts.sort(key=lambda c: (-c[0], c[1]))
                _, canonical_id, canonical_name = counts[0]
                for _, dup_id, dup_name in counts[1:]:
                    affected_ids = [
                        row[0]
                        for row in self._conn.execute(
                            "SELECT entry_id FROM entry_tags WHERE tag_id = ?",
                            (dup_id,),
                        ).fetchall()
                    ]
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO entry_tags (entry_id, tag_id)
                        SELECT entry_id, ? FROM entry_tags WHERE tag_id = ?
                        """,
                        (canonical_id, dup_id),
                    )
                    self._conn.execute(
                        "DELETE FROM entry_tags WHERE tag_id = ?", (dup_id,)
                    )
                    for entry_id in affected_ids:
                        entry_row = self._conn.execute(
                            "SELECT tags_json FROM entries WHERE id = ?", (entry_id,)
                        ).fetchone()
                        if entry_row is None:
                            continue
                        entry_tags = json.loads(entry_row["tags_json"])
                        if dup_name not in entry_tags:
                            continue
                        merged = [t for t in entry_tags if t != dup_name]
                        if canonical_name not in merged:
                            merged.append(canonical_name)
                        self._conn.execute(
                            "UPDATE entries SET tags_json = ?, updated_at = ?"
                            " WHERE id = ?",
                            (json.dumps(merged, ensure_ascii=False), now, entry_id),
                        )
                    self._conn.execute("DELETE FROM tags WHERE id = ?", (dup_id,))
                self._conn.execute(
                    "UPDATE tags SET normalized = ? WHERE id = ?",
                    (key, canonical_id),
                )
            else:
                ((tag_id, _),) = variants
                self._conn.execute(
                    "UPDATE tags SET normalized = ? WHERE id = ?", (key, tag_id)
                )

        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_normalized ON tags(normalized)"
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
    def get_metrics(self) -> dict[str, int]:
        """Return read-activity metrics used by the Collecta dashboard.

        Counts entries whose ``read_at`` timestamp falls within the current
        calendar week (Sun-Sat), month, and year respectively.

        Returns:
            Dict with keys ``reads_week``, ``reads_month``, ``reads_year``.
        """
        from datetime import timedelta

        now = datetime.now(tz=UTC)
        week_start = (now - timedelta(days=(now.weekday() + 1) % 7)).date().isoformat()
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
    def update_tags(self, entry_id: int, tags: list[str]) -> None:
        """Replace an entry's tags and keep the tags/entry_tags tables in sync.

        Each name is resolved to its canonical row via a case-insensitive
        (``casefold``) identity lookup — entering "PYTHON" when "Python"
        already exists reuses that row rather than creating a
        case-duplicate; the first-seen display casing sticks.

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
            key = name.casefold()
            row = self._conn.execute(
                "SELECT id FROM tags WHERE normalized = ?", (key,)
            ).fetchone()
            if row is None:
                cur = self._conn.execute(
                    "INSERT INTO tags (name, normalized) VALUES (?, ?)", (name, key)
                )
                tag_id = cur.lastrowid
            else:
                tag_id = row["id"]
            self._conn.execute(
                "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                (entry_id, tag_id),
            )
        self._conn.commit()

    @_synchronized
    def soft_delete(self, entry_id: int) -> None:
        """Mark an entry as deleted without removing it from the database.

        Args:
            entry_id: Target row id.
        """
        self.update_status(entry_id, "deleted")

    @_synchronized
    def hard_delete(self, entry_id: int) -> None:
        """Permanently remove an entry and all its associations from the database.

        Removes entry_tags rows, removes the FTS index row, and deletes the
        entry row. Does not touch the vault file — caller is responsible
        for file removal.

        Args:
            entry_id: Target row id.
        """
        self._conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
        self._conn.execute("DELETE FROM entries_fts WHERE rowid = ?", (entry_id,))
        self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self._conn.commit()

    @_synchronized
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

    @_synchronized
    def get_entries_by_ids(self, entry_ids: list[int]) -> list[EntryRecord]:
        """Fetch multiple entries in a single batched query.

        Args:
            entry_ids: Row ids to fetch.

        Returns:
            Matching ``EntryRecord`` objects. Ids with no matching row are
            silently omitted; result order is not guaranteed to match
            *entry_ids*.
        """
        if not entry_ids:
            return []
        placeholders = ",".join("?" for _ in entry_ids)
        rows = self._conn.execute(
            f"SELECT * FROM entries WHERE id IN ({placeholders})", entry_ids
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    @_synchronized
    def get_entry_ids_by_tag(self, tag: str) -> list[int]:
        """Return IDs of all entries associated with *tag* — a true union.

        Unions two sources so an entry shows up regardless of which
        mechanism it uses:

        1. **Structural tags** (``entry_tags``/``tags``), matched via
           ``tags.normalized`` (``casefold`` identity) — same convention
           as :meth:`update_tags`/:meth:`list_tags`.
        2. **Content hashtags** (``backlink_refs``), matched via
           ``casefold`` — the same identity content hashtags are stored
           under at index time (see :func:`~analecta.markdown.backlinks.parse_refs`).

        An entry that carries *tag* both structurally and as a content
        hashtag is only counted once.

        Args:
            tag: Tag name to look up.

        Returns:
            Sorted, deduplicated list of entry IDs. Empty if neither a
            structural tag nor a matching content hashtag exists.
        """
        structural_rows = self._conn.execute(
            """
            SELECT et.entry_id
            FROM entry_tags et
            JOIN tags t ON et.tag_id = t.id
            WHERE t.normalized = ?
            """,
            (tag.casefold(),),
        ).fetchall()

        hashtag_rows = self._conn.execute(
            """
            SELECT DISTINCT source_id
            FROM backlink_refs
            WHERE target_text = ? AND is_hashtag = 1
            """,
            (tag.casefold(),),
        ).fetchall()

        ids = {row[0] for row in structural_rows} | {row[0] for row in hashtag_rows}
        return sorted(ids)

    @_synchronized
    def get_body_hashtag_entry_ids(self, name: str) -> list[int]:
        """Return IDs of entries whose body contains *name* as a literal ``#hashtag``.

        Unlike :meth:`get_entry_ids_by_tag`, this only counts the
        content-hashtag side — an entry where *name* exists solely as a
        structural ``entry_tags`` row is excluded. Used to find entries
        whose body text would survive a structural tag deletion and
        otherwise resurrect the tag identity.

        A tag name only has a legitimate hashtag form when
        :func:`~analecta.markdown.backlinks.is_valid_hashtag_literal` accepts
        it — false for a symbol- or space-bearing name like ``"C++"`` that
        could never appear verbatim as ``#C++``. Returns an empty list
        rather than spuriously matching a same-named hashtag for those —
        same collision-avoidance rule documented on
        :meth:`get_entry_ids_by_tag`.

        Args:
            name: Tag name to look up.

        Returns:
            Sorted, deduplicated list of entry IDs. Empty if *name* has no
            valid hashtag form or no matching content hashtag exists.
        """
        from analecta.markdown.backlinks import is_valid_hashtag_literal

        if not is_valid_hashtag_literal(name):
            return []
        rows = self._conn.execute(
            """
            SELECT DISTINCT source_id
            FROM backlink_refs
            WHERE target_text = ? AND is_hashtag = 1
            """,
            (name.casefold(),),
        ).fetchall()
        return sorted(row[0] for row in rows)

    @_synchronized
    def get_content_hashtags_for_entries(
        self, entry_ids: list[int]
    ) -> dict[int, list[str]]:
        """Return each entry's own content hashtags, keyed by entry id.

        Unlike :meth:`get_hashtag_connections`, this has no peer
        requirement — it's the raw set of hashtags an entry's own
        Markdown contains, regardless of whether any other entry shares
        them. Used to let the reading view's tag list include hashtags
        that were never also assigned as a structural tag.

        Hashtags are stored lowercase (normalized at index time), but the
        display name returned here is resolved against any existing
        *structural* tag with the same identity, so a hashtag that's also
        a curated structural tag elsewhere in the vault (e.g. "Python")
        displays with that casing instead of the raw lowercase form —
        this is what keeps the reading-view tag list case-consistent with
        the rest of the UI.

        Args:
            entry_ids: Entry ids to look up.

        Returns:
            Dict mapping entry id to a sorted list of distinct,
            canonically-cased hashtag texts. Entries with none are
            omitted from the dict.
        """
        if not entry_ids:
            return {}
        placeholders = ",".join("?" for _ in entry_ids)
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT source_id, target_text
            FROM backlink_refs
            WHERE is_hashtag = 1 AND source_id IN ({placeholders})
            """,
            entry_ids,
        ).fetchall()
        if not rows:
            return {}
        display_names = dict(
            self._conn.execute("SELECT normalized, name FROM tags").fetchall()
        )
        result: dict[int, list[str]] = {}
        for source_id, target_text in rows:
            result.setdefault(source_id, []).append(
                display_names.get(target_text, target_text)
            )
        for tags in result.values():
            tags.sort()
        return result

    @_synchronized
    def create_tag(self, name: str) -> tuple[str, int]:
        """Create a standalone tag with no entries.

        Args:
            name: Tag name to create. No-ops if a tag with the same
                case-insensitive identity already exists.

        Returns:
            Tuple of ``(canonical_name, count)``. If a tag with this
            case-insensitive identity already exists, returns its existing
            display name and current structural+hashtag union count rather
            than creating a duplicate. Otherwise creates the tag and
            returns ``(name, count)`` — count may be nonzero if content
            hashtags already reference this identity.

        Raises:
            InvalidTagNameError: If *name* isn't a valid bare hashtag token
                (contains symbols or spaces), so it could never be written
                as a literal ``#name`` in an entry body.
        """
        from analecta.markdown.backlinks import is_valid_hashtag_literal

        if not is_valid_hashtag_literal(name):
            raise InvalidTagNameError(
                f"Cannot create tag '{name}': not a valid hashtag name "
                "(no spaces or symbols other than _ - ' ~ ^)."
            )

        key = name.casefold()
        row = self._conn.execute(
            "SELECT name FROM tags WHERE normalized = ?", (key,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO tags (name, normalized) VALUES (?, ?)", (name, key)
            )
            self._conn.commit()
            canonical = name
        else:
            canonical = row["name"]
        return canonical, len(self.get_entry_ids_by_tag(canonical))

    @_synchronized
    def rename_tag(
        self, old_name: str, new_name: str, *, merge: bool = False
    ) -> tuple[str, int] | None:
        """Rename a tag globally, optionally merging into an existing tag.

        Updates the tags table and re-serialises ``tags_json`` in all
        affected entries. The destination is matched case-insensitively
        against existing *structural* tags only — renaming into an
        identity that today exists only as a content hashtag is allowed
        unconditionally; it simply lets the renamed tag unify with those
        content occurrences going forward. Renaming into an identity that
        already exists as *another structural tag* is a merge — two
        curated tags collapsing into one, irreversible — and requires
        ``merge=True``; without it, raises instead of silently combining
        them (a typo into an existing tag name must not collapse two
        categories by accident).

        When merging two structural tags, the **destination's pre-existing
        display casing wins** (matches the sticky-first-seen convention
        used everywhere else) — *new_name*'s own casing is only used to
        *find* the destination (case-insensitively), never to overwrite it.
        Every entry carrying the old tag is reassigned to the destination's
        ``entry_tags`` row (``INSERT OR IGNORE`` so an entry already
        carrying both tags doesn't collide), the old ``tags`` row is
        deleted, and ``tags_json`` is rewritten with the old name replaced
        by the destination's canonical name, de-duplicated (an entry that
        already had both tags must not end up with the destination name
        listed twice).

        Also migrates literal ``#hashtag`` occurrences in entry bodies that
        share *old_name*'s identity — unlike :meth:`delete_tag`, which
        neutralizes survivor text (backticks it, since deletion should
        sever the identity), rename rewrites ``#old`` to the destination
        tag's canonical name in place via
        :func:`~analecta.markdown.backlinks.rename_hashtag_occurrences`, so
        the body text keeps meaning the same tag instead of splitting into
        two identities after the rename. Works even when *old_name* has no
        structural row at all (a purely content-hashtag identity, as shown
        in the Sidebar's true-union tag list) — same content-only handling
        as :meth:`delete_tag`; that path is unaffected by *merge* since a
        content-only identity has no structural row to conflict with.

        Args:
            old_name: Current tag name. Matched case-insensitively
                (``casefold``) against both structural tags and content
                hashtags.
            new_name: Replacement tag name, or the (case-insensitive)
                target of a merge.
            merge: Required to proceed when *new_name*'s identity already
                exists as another structural tag. Ignored otherwise.

        Returns:
            Tuple of ``(canonical_name, count)`` — *canonical_name* is
            *new_name* as given, unless this was a merge, in which case
            it's the destination's pre-existing display name. *count* is
            the renamed tag's current structural+hashtag union count,
            computed after both the structural update and any body-text
            migration — or ``None`` if neither a structural tag nor a
            content hashtag with *old_name*'s identity exists (no-op).

        Raises:
            InvalidTagNameError: If *new_name* isn't a valid bare hashtag
                token (contains symbols or spaces) — every tag must stay
                writable as a literal ``#name``, structural or not.
            ValueError: If a structural tag with *new_name*'s
                case-insensitive identity already exists and *merge* is
                not ``True``.
        """
        from analecta.markdown.backlinks import (
            is_valid_hashtag_literal,
            rename_hashtag_occurrences,
        )

        old_key = old_name.casefold()
        tag_row = self._conn.execute(
            "SELECT id FROM tags WHERE normalized = ?", (old_key,)
        ).fetchone()
        hashtag_entry_ids = self.get_body_hashtag_entry_ids(old_name)

        if tag_row is None and not hashtag_entry_ids:
            return None

        if not is_valid_hashtag_literal(new_name):
            # Same message regardless of whether old_name also has literal
            # #hashtag occurrences in entry bodies: the rename fails purely
            # because new_name is invalid, not because of those occurrences
            # (they'd migrate fine under any valid name), so the reason
            # given must not vary with an unrelated fact.
            raise InvalidTagNameError(
                f"Cannot rename to '{new_name}': not a valid hashtag name "
                "(no spaces or symbols other than _ - ' ~ ^)."
            )

        new_key = new_name.casefold()
        dest_row = None
        if tag_row is not None:
            tag_id = tag_row["id"]
            dest_row = self._conn.execute(
                "SELECT id, name FROM tags WHERE normalized = ? AND id != ?",
                (new_key, tag_id),
            ).fetchone()
            if dest_row is not None and not merge:
                raise ValueError(f"Tag '{new_name}' already exists")

        canonical_name = dest_row["name"] if dest_row is not None else new_name

        if tag_row is not None:
            tag_id = tag_row["id"]
            entry_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT entry_id FROM entry_tags WHERE tag_id = ?", (tag_id,)
                ).fetchall()
            ]
            if dest_row is None:
                self._conn.execute(
                    "UPDATE tags SET name = ?, normalized = ? WHERE id = ?",
                    (new_name, new_key, tag_id),
                )
            else:
                dest_id = dest_row["id"]
                for eid in entry_ids:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id)"
                        " VALUES (?, ?)",
                        (eid, dest_id),
                    )
                self._conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (tag_id,))
                self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            now = _now()
            for eid in entry_ids:
                row = self._conn.execute(
                    "SELECT tags_json FROM entries WHERE id = ?", (eid,)
                ).fetchone()
                if row:
                    tags: list[str] = []
                    seen: set[str] = set()
                    for t in json.loads(row["tags_json"]):
                        replacement = canonical_name if t.casefold() == old_key else t
                        key = replacement.casefold()
                        if key in seen:
                            continue
                        seen.add(key)
                        tags.append(replacement)
                    self._conn.execute(
                        "UPDATE entries SET tags_json = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False), now, eid),
                    )
            self._conn.commit()

        if hashtag_entry_ids:
            target_normalized = old_name.casefold()
            for eid in hashtag_entry_ids:
                entry = self.get_entry(eid)
                if entry is None:
                    continue
                file_path = Path(entry.file_path)
                if not file_path.exists():
                    continue
                markdown = file_path.read_text(encoding="utf-8")
                rewritten, changed = rename_hashtag_occurrences(
                    markdown, target_normalized, canonical_name
                )
                if changed:
                    file_path.write_text(rewritten, encoding="utf-8")
                # Reindex unconditionally, even when the body no longer
                # contains the literal hashtag: hashtag_entry_ids comes from
                # the backlink_refs cache, which can already be stale (e.g.
                # the file was edited outside the app). Gating this on
                # `changed` left such rows permanently stuck, since nothing
                # else ever re-derives backlink_refs from the current body.
                self.index_backlinks(eid)

        return canonical_name, len(self.get_entry_ids_by_tag(canonical_name))

    @_synchronized
    def delete_tag(self, name: str) -> None:
        """Delete a tag globally.

        Removes it from the tags table, entry_tags, and re-serialises
        ``tags_json`` in all affected entries. Also neutralizes any literal
        ``#hashtag`` occurrence in an entry's Markdown body that shares this
        tag's identity — wrapped in backticks so
        :meth:`index_backlinks` treats it as inline code on the next
        re-index, instead of letting the structural deletion leave a
        surviving body mention that resurrects the tag (lowercase) the next
        time that entry's file is re-indexed. See
        :func:`analecta.markdown.backlinks.neutralize_hashtag_occurrences`.
        Works even when *name* has no structural row at all — a
        content-hashtag-only tag (as shown in the Sidebar's true-union tag
        list) is fully removable too.

        Args:
            name: Tag name to delete. Matched case-insensitively
                (``casefold``) against both structural tags and content
                hashtags. Does nothing if neither exists.
        """
        from analecta.markdown.backlinks import neutralize_hashtag_occurrences

        key = name.casefold()
        tag_row = self._conn.execute(
            "SELECT id FROM tags WHERE normalized = ?", (key,)
        ).fetchone()
        hashtag_entry_ids = self.get_body_hashtag_entry_ids(name)

        if tag_row is None and not hashtag_entry_ids:
            return

        if tag_row is not None:
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
                    tags = [
                        t for t in json.loads(row["tags_json"]) if t.casefold() != key
                    ]
                    self._conn.execute(
                        "UPDATE entries SET tags_json = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False), now, eid),
                    )
            self._conn.execute("DELETE FROM entry_tags WHERE tag_id = ?", (tag_id,))
            self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self._conn.commit()

        if hashtag_entry_ids:
            target_normalized = name.casefold()
            for eid in hashtag_entry_ids:
                entry = self.get_entry(eid)
                if entry is None:
                    continue
                file_path = Path(entry.file_path)
                if not file_path.exists():
                    continue
                markdown = file_path.read_text(encoding="utf-8")
                rewritten, wrapped = neutralize_hashtag_occurrences(
                    markdown, target_normalized
                )
                if wrapped:
                    file_path.write_text(rewritten, encoding="utf-8")
                # Reindex unconditionally — see the matching comment in
                # rename_tag. A stale backlink_refs row (body already
                # doesn't contain the hashtag) must still be cleared even
                # though there's nothing left to wrap.
                self.index_backlinks(eid)

    @_synchronized
    def index_backlinks(self, source_id: int) -> None:
        """Re-index all outgoing backlink refs for *source_id*.

        Reads the entry's Markdown file, parses ``[[wikilinks]]`` and
        ``#hashtags``, clears any previously indexed refs for this source,
        and inserts fresh rows into ``backlink_refs``. Also stamps
        ``entries.indexed_mtime`` with the file's current mtime, so
        :meth:`reconcile_stale_entries` can tell this entry is caught up.

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
        mtime = file_path.stat().st_mtime

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
        self._conn.execute(
            "UPDATE entries SET indexed_mtime = ? WHERE id = ?", (mtime, source_id)
        )
        self._conn.commit()

    @_synchronized
    def reconcile_stale_entries(self, *, force: bool = False) -> int:
        """Re-derive backlinks and FTS content for entries edited outside the app.

        Nothing re-scans an entry's Markdown file on its own — tag rename/
        delete only reindex the entries their own (possibly stale)
        ``backlink_refs`` cache points them to, and the editor only writes
        FTS/backlink data on its own explicit save. An entry edited
        directly on disk (another editor, a sync tool, a script) is never
        picked up by either path, so its cached hashtag identities and
        search content silently drift from the file's real content.

        Compares each entry's current file mtime against
        ``entries.indexed_mtime`` (stamped by :meth:`index_backlinks` and
        :meth:`update_fts_content`); a mismatch — or a ``NULL`` value, e.g.
        a row that predates this column — reindexes that entry's
        ``backlink_refs`` and ``entries_fts`` row from the file's current
        content. Entries whose file no longer exists are left untouched.

        Called unconditionally (``force=False``) once per sidecar startup.
        Mtime is a heuristic, not a guarantee: tools that preserve or
        backdate mtime on write (some sync clients, ``cp -p``, ``rsync
        -t``) can leave a real content change undetected. ``force=True`` —
        used by the manual "Rescan vault" action — still reindexes every
        entry regardless of its recorded mtime, precisely to give an
        escape hatch for that gap and for edits made while the sidecar is
        already running.

        The returned count always means "entries whose mtime had drifted"
        (mtime mismatch or never-indexed), never "entries reindexed" —
        under ``force=True`` those two numbers diverge, since every entry
        gets reindexed but only the mtime-drifted ones are counted. This
        keeps the manual action's reported number meaningful ("N entries
        were actually out of sync") instead of degenerating to "the size
        of your vault" on every click. The one gap this doesn't close: a
        force-triggered reindex that *does* pick up a real content change
        whose mtime happened to be preserved is still silently counted as
        0 — mtime is the only staleness signal available short of content
        hashing, which this deliberately doesn't add.

        Args:
            force: Reindex every entry regardless of its recorded mtime.

        Returns:
            Number of entries whose recorded mtime didn't match the file
            on disk (or had never been indexed) — not the number of
            entries actually reindexed, which under ``force=True`` is
            every entry in the vault.
        """
        rows = self._conn.execute(
            "SELECT id, file_path, indexed_mtime FROM entries"
        ).fetchall()

        stale_count = 0
        for row in rows:
            file_path = Path(row["file_path"])
            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                continue
            is_stale = row["indexed_mtime"] is None or mtime != row["indexed_mtime"]
            if not force and not is_stale:
                continue
            entry_id = row["id"]
            markdown = file_path.read_text(encoding="utf-8")
            entry = self.get_entry(entry_id)
            if entry is None:
                continue
            self.update_fts_content(entry_id, entry.title, markdown)
            self.index_backlinks(entry_id)
            if is_stale:
                stale_count += 1
        return stale_count

    @_synchronized
    def get_backlinks(self, target_id: int) -> list[BacklinkRecord]:
        """Return all entries that link to *target_id*.

        Resolves ``backlink_refs`` against the current ``entries`` table.
        Wikilinks are matched by lowercased title; hashtags by
        :func:`~analecta.markdown.hashtags.title_to_hashtag_key`, the same
        casefold-based identity hashtags themselves use.

        Args:
            target_id: ID of the entry to query backlinks for.

        Returns:
            List of :class:`BacklinkRecord` objects ordered by source title
            then document position.
        """
        target_row = self._conn.execute(
            "SELECT title FROM entries WHERE id = ?", (target_id,)
        ).fetchone()
        if target_row is None:
            return []

        title_lower = target_row["title"].lower()
        title_slug = title_to_hashtag_key(target_row["title"])

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

    @_synchronized
    def get_all_titles(self) -> list[tuple[int, str]]:
        """Return the id and title of every entry.

        Used for client-side title-to-id lookups (e.g. resolving
        ``[[wikilinks]]`` to a real entry without a per-render round trip).

        Returns:
            List of ``(id, title)`` tuples ordered by id.
        """
        rows = self._conn.execute(
            "SELECT id, title FROM entries ORDER BY id ASC"
        ).fetchall()
        return [(row["id"], row["title"]) for row in rows]

    @_synchronized
    def get_outgoing_links(self, source_id: int) -> list[OutgoingLinkRecord]:
        """Return all entries that *source_id* links to via wikilinks or hashtags.

        Resolves *source_id*'s own ``backlink_refs`` rows against the current
        ``entries`` table, using the same title-matching rules as
        :meth:`get_backlinks` and :meth:`get_subgraph`. References that don't
        resolve to a real entry (unresolved wikilinks, hashtags with no
        matching entry) are skipped — there is nothing to navigate to.

        Args:
            source_id: ID of the entry to query outgoing links for.

        Returns:
            List of :class:`OutgoingLinkRecord` ordered by target title then
            document position.
        """
        entry_rows = self._conn.execute("SELECT id, title FROM entries").fetchall()
        lower_title_to_entry: dict[str, tuple[int, str]] = {
            row["title"].lower(): (row["id"], row["title"]) for row in entry_rows
        }
        slug_to_entry: dict[str, tuple[int, str]] = {
            title_to_hashtag_key(row["title"]): (row["id"], row["title"])
            for row in entry_rows
        }

        rows = self._conn.execute(
            """
            SELECT target_text, is_hashtag, heading, pre, highlight, post
            FROM backlink_refs
            WHERE source_id = ?
            ORDER BY id ASC
            """,
            (source_id,),
        ).fetchall()

        results: list[OutgoingLinkRecord] = []
        for row in rows:
            target_text: str = row["target_text"]
            is_hashtag: bool = bool(row["is_hashtag"])
            resolved = (
                slug_to_entry.get(target_text)
                if is_hashtag
                else lower_title_to_entry.get(target_text)
            )
            if resolved is None or resolved[0] == source_id:
                continue
            target_id, target_title = resolved
            results.append(
                OutgoingLinkRecord(
                    target_id=target_id,
                    target_title=target_title,
                    heading=row["heading"],
                    pre=row["pre"],
                    highlight=row["highlight"],
                    post=row["post"],
                )
            )

        results.sort(key=lambda r: (r.target_title.lower(), r.target_id))
        return results

    @_synchronized
    def get_hashtag_connections(self, source_id: int) -> list[HashtagConnectionGroup]:
        """Return other entries grouped by shared tag — a true union.

        Like :meth:`list_tags`, gathers every tag identity *source_id* itself
        carries — structural (``entry_tags``, keyed by ``tags.normalized``)
        or content (its own ``backlink_refs`` hashtags) — then, for each
        identity, finds peer entries that carry it through *either*
        mechanism (same
        ``entry_tags UNION backlink_refs`` pattern as :meth:`get_subgraph`'s
        neighbor query). An entry tagged "python" structurally and one that
        only ever writes ``#python`` in its body correctly show up as
        connections of each other, regardless of which mechanism either side
        uses. Display name resolves via the same ``normalized -> name`` map
        as :meth:`get_graph`/:meth:`get_subgraph` — structural casing wins
        when a structural counterpart exists anywhere in the vault, even if
        *source_id* itself only has the tag as a lowercase content hashtag.

        Groups are sorted by normalized key; entries within each group are
        sorted by title. Empty groups (no peer entries) are excluded.

        Args:
            source_id: ID of the entry to query connections for.

        Returns:
            List of :class:`HashtagConnectionGroup` ordered by normalized key.
        """
        structural_rows = self._conn.execute(
            "SELECT t.normalized FROM entry_tags et"
            " JOIN tags t ON t.id = et.tag_id WHERE et.entry_id = ?",
            (source_id,),
        ).fetchall()
        hashtag_rows = self._conn.execute(
            "SELECT DISTINCT target_text FROM backlink_refs"
            " WHERE source_id = ? AND is_hashtag = 1",
            (source_id,),
        ).fetchall()
        tag_keys: set[str] = {row[0] for row in structural_rows} | {
            row[0] for row in hashtag_rows
        }
        if not tag_keys:
            return []

        tag_display: dict[str, str] = dict(
            self._conn.execute("SELECT normalized, name FROM tags").fetchall()
        )

        merged: dict[str, tuple[str, list[EntryRecord]]] = {}
        for key in tag_keys:
            peer_rows = self._conn.execute(
                """
                SELECT e.*
                FROM entries e
                WHERE e.id IN (
                    SELECT et.entry_id
                    FROM entry_tags et
                    JOIN tags t ON t.id = et.tag_id
                    WHERE t.normalized = ? AND et.entry_id != ?
                    UNION
                    SELECT br.source_id
                    FROM backlink_refs br
                    WHERE br.target_text = ? AND br.is_hashtag = 1
                      AND br.source_id != ?
                )
                ORDER BY e.title ASC
                """,
                (key, source_id, key, source_id),
            ).fetchall()
            peers = [_row_to_entry(r) for r in peer_rows]
            if peers:
                merged[key] = (tag_display.get(key, key), peers)

        return [
            HashtagConnectionGroup(hashtag=display, entries=entries)
            for _, (display, entries) in sorted(merged.items())
        ]

    @_synchronized
    def get_subgraph(
        self, focus_id: int
    ) -> tuple[list[GraphNodeRecord], list[GraphEdgeRecord]] | None:
        """Return the 1-hop neighbourhood subgraph centred on *focus_id*.

        Resolves both outlinks (entries that *focus_id* links to) and inlinks
        (entries that link to *focus_id*) using the same title-matching rules
        as :meth:`get_backlinks` and :meth:`get_graph`. Tag connections are a
        true union of structural ``entry_tags`` and content hashtags — an
        entry linked to the focus only through the other mechanism still
        appears as a neighbor of the shared tag node. The focus entry is
        always present as a node even when it has no connections. Returns
        ``None`` if *focus_id* does not exist.

        A hashtag resolving to an entry's title always keeps its own tag
        node/edge alongside the entry edge (see :meth:`get_graph`). This
        holds for the focus's own outlinks. For an *inbound* hashtag that
        happens to resolve to the focus's own title, the referencing entry
        still gets its tag node/edge — but it is not folded into the tag-hub
        fan-out (no unrelated vault-wide neighbors are pulled in) and no
        synthetic focus->tag edge is added, since the focus never authored
        that hashtag. This makes ``get_subgraph()`` locally narrower than
        ``get_graph()`` for this one case, by design.

        Args:
            focus_id: ID of the focal entry.

        Returns:
            Tuple ``(nodes, edges)`` or ``None`` if the entry is missing.
        """
        focus_row = self._conn.execute(
            "SELECT title, source_type FROM entries WHERE id = ?", (focus_id,)
        ).fetchone()
        if focus_row is None:
            return None

        focus_title: str = focus_row[0]
        focus_source_type: str = focus_row[1]

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
            title_to_hashtag_key(title): eid for eid, (title, _) in entries.items()
        }

        node_map: dict[str, GraphNodeRecord] = {}
        edge_weights: dict[tuple[str, str], int] = {}

        focus_node_id = f"entry:{focus_id}"
        node_map[focus_node_id] = GraphNodeRecord(
            node_id=focus_node_id,
            label=focus_title,
            kind="entry",
            source_type=focus_source_type,
        )

        tag_display: dict[str, str] = dict(
            self._conn.execute("SELECT normalized, name FROM tags").fetchall()
        )

        def ensure_tag_node(key: str) -> str:
            node_id = f"tag:{key}"
            if node_id not in node_map:
                node_map[node_id] = GraphNodeRecord(
                    node_id=node_id,
                    label=f"#{tag_display.get(key, key)}",
                    kind="tag",
                    source_type=None,
                )
            return node_id

        focus_tag_keys: set[str] = set()

        # Outlinks: refs where focus_id is the source
        out_refs = self._conn.execute(
            "SELECT target_text, is_hashtag FROM backlink_refs WHERE source_id = ?",
            (focus_id,),
        ).fetchall()
        for ref in out_refs:
            target_text: str = ref[0]
            is_hashtag: bool = bool(ref[1])
            if not is_hashtag:
                target_id = lower_title_to_id.get(target_text)
                if target_id is None or target_id == focus_id:
                    continue
                target_node_id = f"entry:{target_id}"
                if target_node_id not in node_map:
                    t_title, t_src_type = entries[target_id]
                    node_map[target_node_id] = GraphNodeRecord(
                        node_id=target_node_id,
                        label=t_title,
                        kind="entry",
                        source_type=t_src_type,
                    )
                key = (focus_node_id, target_node_id)
                edge_weights[key] = edge_weights.get(key, 0) + 1
            else:
                # A hashtag always keeps its own tag node/edge — and, if it
                # also resolves to an existing entry's title, gets a second,
                # independent entry->entry edge alongside it (see get_graph).
                tag_node_id = ensure_tag_node(target_text)
                focus_tag_keys.add(target_text)
                tag_edge_key = (focus_node_id, tag_node_id)
                edge_weights[tag_edge_key] = edge_weights.get(tag_edge_key, 0) + 1

                target_entry_id = slug_to_id.get(target_text)
                if target_entry_id is not None and target_entry_id != focus_id:
                    target_node_id = f"entry:{target_entry_id}"
                    if target_node_id not in node_map:
                        t_title, t_src_type = entries[target_entry_id]
                        node_map[target_node_id] = GraphNodeRecord(
                            node_id=target_node_id,
                            label=t_title,
                            kind="entry",
                            source_type=t_src_type,
                        )
                    key = (focus_node_id, target_node_id)
                    edge_weights[key] = edge_weights.get(key, 0) + 1

        # Inlinks: entries whose backlink_refs resolve to focus_id
        title_lower = focus_title.lower()
        title_slug = title_to_hashtag_key(focus_title)
        in_rows = self._conn.execute(
            """
            SELECT br.source_id, br.target_text, br.is_hashtag
            FROM backlink_refs br
            JOIN entries e ON e.id = br.source_id
            WHERE e.id != ?
              AND (
                (br.is_hashtag = 0 AND br.target_text = ?)
                OR (br.is_hashtag = 1 AND br.target_text = ?)
              )
            """,
            (focus_id, title_lower, title_slug),
        ).fetchall()
        for row in in_rows:
            src_id: int = row[0]
            ref_target_text: str = row[1]
            ref_is_hashtag: bool = bool(row[2])
            src_node_id = f"entry:{src_id}"
            if src_node_id not in node_map:
                s_title, s_src_type = entries[src_id]
                node_map[src_node_id] = GraphNodeRecord(
                    node_id=src_node_id,
                    label=s_title,
                    kind="entry",
                    source_type=s_src_type,
                )
            key = (src_node_id, focus_node_id)
            edge_weights[key] = edge_weights.get(key, 0) + 1

            # A hashtag that happens to resolve to the focus entry's own
            # title still keeps its own tag node/edge on the referencing
            # entry (src -> tag), matching get_graph()'s "always both"
            # rule. Deliberately NOT added to focus_tag_keys — that would
            # fan out to every unrelated entry sharing the tag elsewhere in
            # the vault, which only get_graph()'s vault-wide view should do.
            # Deliberately no synthetic focus->tag edge either — the focus
            # never authored this hashtag just by sharing its title.
            if ref_is_hashtag:
                tag_node_id = ensure_tag_node(ref_target_text)
                tag_edge_key = (src_node_id, tag_node_id)
                edge_weights[tag_edge_key] = edge_weights.get(tag_edge_key, 0) + 1

        # Tag-hub: structured entry_tags for the focus entry, keyed by
        # tags.normalized (an aggressive ASCII-slugify of the tag name would
        # strip symbols/accents and drift from this identity — see get_graph).
        structural_tag_rows = self._conn.execute(
            """
            SELECT t.normalized
            FROM entry_tags et
            JOIN tags t ON t.id = et.tag_id
            WHERE et.entry_id = ?
            """,
            (focus_id,),
        ).fetchall()
        for srow in structural_tag_rows:
            tag_key: str = srow[0]
            focus_tag_keys.add(tag_key)
            tag_node_id = ensure_tag_node(tag_key)
            key = (focus_node_id, tag_node_id)
            edge_weights[key] = edge_weights.get(key, 0) + 1

        # Neighbors sharing any of the focus entry's tags — a true union of
        # structural entry_tags and content hashtags by normalized identity,
        # so an entry linked only through one mechanism still shows up as a
        # neighbor of one sharing the same tag through the other.
        for tag_key in focus_tag_keys:
            tag_node_id = f"tag:{tag_key}"
            neighbor_rows = self._conn.execute(
                """
                SELECT et.entry_id
                FROM entry_tags et
                JOIN tags t ON t.id = et.tag_id
                WHERE t.normalized = ? AND et.entry_id != ?
                UNION
                SELECT source_id
                FROM backlink_refs
                WHERE target_text = ? AND is_hashtag = 1 AND source_id != ?
                """,
                (tag_key, focus_id, tag_key, focus_id),
            ).fetchall()
            for nrow in neighbor_rows:
                neighbor_id: int = nrow[0]
                if neighbor_id not in entries:
                    continue
                neighbor_node_id = f"entry:{neighbor_id}"
                if neighbor_node_id not in node_map:
                    n_title, n_src_type = entries[neighbor_id]
                    node_map[neighbor_node_id] = GraphNodeRecord(
                        node_id=neighbor_node_id,
                        label=n_title,
                        kind="entry",
                        source_type=n_src_type,
                    )
                nkey = (neighbor_node_id, tag_node_id)
                edge_weights[nkey] = edge_weights.get(nkey, 0) + 1

        nodes = list(node_map.values())
        edges = [
            GraphEdgeRecord(source=s, target=t, weight=w)
            for (s, t), w in edge_weights.items()
        ]
        return nodes, edges

    @_synchronized
    def list_tags(self) -> list[tuple[str, int]]:
        """Return all tags — structural and content — as a true union.

        A tag's entry set is the union of its structural ``entry_tags``
        links (identity = ``tags.normalized``, a ``casefold`` of the
        display name) and its content-hashtag ``backlink_refs`` rows
        (already normalized at index time), deduplicated by entry id —
        an entry that carries a tag both structurally and as a content
        hashtag is only counted once. A standalone structural tag with
        zero links still appears (count 0). Structural display casing
        always wins over a bare hashtag's lowercase form.

        Returns:
            List of ``(name, count)`` tuples, sorted alphabetically by
            name (case-insensitive).
        """
        groups: dict[str, tuple[str, set[int]]] = {}

        for normalized, name in self._conn.execute("SELECT normalized, name FROM tags"):
            groups.setdefault(normalized, (name, set()))

        structural_rows = self._conn.execute(
            """
            SELECT t.normalized, t.name, et.entry_id
            FROM entry_tags et
            JOIN tags t ON t.id = et.tag_id
            """
        ).fetchall()
        for normalized, name, entry_id in structural_rows:
            _, ids = groups.setdefault(normalized, (name, set()))
            ids.add(entry_id)

        hashtag_rows = self._conn.execute(
            "SELECT target_text, source_id FROM backlink_refs WHERE is_hashtag = 1"
        ).fetchall()
        for target_text, source_id in hashtag_rows:
            _, ids = groups.setdefault(target_text, (target_text, set()))
            ids.add(source_id)

        return sorted(
            ((display, len(ids)) for display, ids in groups.values()),
            key=lambda pair: pair[0].casefold(),
        )

    @_synchronized
    def get_graph(
        self,
    ) -> tuple[list[GraphNodeRecord], list[GraphEdgeRecord]]:
        """Return all connected nodes and weighted edges for the vault graph.

        Resolves ``backlink_refs`` against the current ``entries`` table using
        the same title-matching rules as :meth:`get_backlinks`. Wikilinks that
        do not resolve to an existing entry are skipped. Every hashtag always
        produces its own tag node (``tag:{normalized}``) and edge; if the
        hashtag *also* resolves to an existing entry's title, a second,
        independent entry->entry edge is added alongside it — the two are not
        mutually exclusive. A structural tag and a content hashtag of the
        same identity share one node — see :class:`GraphNodeRecord`. Multiple
        occurrences of the same source→target pair are collapsed into a
        single weighted edge. Entries with no connections (isolated nodes)
        are excluded.

        Returns:
            Tuple of ``(nodes, edges)``.  Nodes include both ``entry:`` and
            ``tag:`` kinds.  Edges are directed but the frontend may treat them
            as undirected for layout purposes.
        """
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
            title_to_hashtag_key(title): eid for eid, (title, _) in entries.items()
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
                key = (source_node, target_node)
                edge_weights[key] = edge_weights.get(key, 0) + 1
            else:
                # A hashtag always keeps its own tag node/edge — and, if it
                # additionally resolves to an existing entry's title, gets a
                # second, independent entry->entry edge alongside it.
                tag_node = f"tag:{target_text}"
                virtual_tags.add(target_text)
                tag_edge_key = (source_node, tag_node)
                edge_weights[tag_edge_key] = edge_weights.get(tag_edge_key, 0) + 1

                target_id = slug_to_id.get(target_text)
                if target_id is not None and target_id != source_id:
                    target_node = f"entry:{target_id}"
                    key = (source_node, target_node)
                    edge_weights[key] = edge_weights.get(key, 0) + 1

        # Tag-hub edges from structured entry_tags (UI-assigned tags), keyed
        # by tags.normalized so these land on the same tag node as a content
        # hashtag of the same identity. Never re-derive this via an
        # aggressive ASCII-slugify normalizer — it strips symbols and would
        # collide e.g. "C++" with a "#c" hashtag.
        tag_ref_rows = self._conn.execute(
            "SELECT et.entry_id, t.normalized"
            " FROM entry_tags et JOIN tags t ON t.id = et.tag_id"
        ).fetchall()
        for tag_row in tag_ref_rows:
            tagged_entry_id: int = tag_row[0]
            tag_key: str = tag_row[1]
            if tagged_entry_id not in entries:
                continue
            source_node = f"entry:{tagged_entry_id}"
            target_node = f"tag:{tag_key}"
            virtual_tags.add(tag_key)
            key = (source_node, target_node)
            edge_weights[key] = edge_weights.get(key, 0) + 1

        connected_entry_ids: set[int] = set()
        for s, t in edge_weights:
            if s.startswith("entry:"):
                connected_entry_ids.add(int(s[6:]))
            if t.startswith("entry:"):
                connected_entry_ids.add(int(t[6:]))

        tag_display: dict[str, str] = dict(
            self._conn.execute("SELECT normalized, name FROM tags").fetchall()
        )

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
                node_id=f"tag:{key}",
                label=f"#{tag_display.get(key, key)}",
                kind="tag",
                source_type=None,
            )
            for key in virtual_tags
        ]

        edges: list[GraphEdgeRecord] = [
            GraphEdgeRecord(source=s, target=t, weight=w)
            for (s, t), w in edge_weights.items()
        ]

        return nodes, edges

    @_synchronized
    def search(self, query: str) -> list[EntryRecord]:
        """Full-text search across title and content using FTS5.

        Transforms the raw query into a safe prefix-match expression via
        :func:`_fts_prefix_query` so that partial words match (e.g. ``"Rolld"``
        finds ``"Rolldown…"``).

        Args:
            query: Raw user query string.

        Returns:
            Matching entries ordered by relevance (BM25).
        """
        fts_query = _fts_prefix_query(query)
        if not fts_query:
            return []
        rows = self._conn.execute(
            """
            SELECT e.* FROM entries e
            JOIN entries_fts fts ON fts.rowid = e.id
            WHERE entries_fts MATCH ?
            ORDER BY rank
            """,
            (fts_query,),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    @_synchronized
    def get_linked_entries(self, entry_id: int) -> list[EntryRecord]:
        """Return entries listed in *entry_id*'s frontmatter ``linked`` field.

        Reads the entry's Markdown file, parses the ``linked:`` YAML list, and
        looks up each title in the DB (case-insensitive).

        Args:
            entry_id: ID of the entry whose file to inspect.

        Returns:
            List of :class:`EntryRecord` objects for each matched linked title,
            in frontmatter order. Titles that do not match any entry are skipped.
        """
        import yaml

        entry = self.get_entry(entry_id)
        if entry is None:
            return []

        file_path = Path(entry.file_path)
        if not file_path.exists():
            return []

        markdown = file_path.read_text(encoding="utf-8")
        m = _re.match(r"^---\n([\s\S]*?)\n---\n", markdown)
        if not m:
            return []

        try:
            fm_data: dict[str, Any] = yaml.safe_load(m.group(1)) or {}
            linked_titles = [str(t) for t in (fm_data.get("linked") or [])]
        except Exception:
            return []

        result: list[EntryRecord] = []
        for title in linked_titles:
            row = self._conn.execute(
                "SELECT * FROM entries WHERE LOWER(title) = LOWER(?)", (title,)
            ).fetchone()
            if row:
                result.append(_row_to_entry(row))
        return result

    @_synchronized
    def add_link(self, source_id: int, target_id: int) -> None:
        """Create a bidirectional explicit link between two entries.

        Writes the target's title into the source's frontmatter ``linked`` field
        and vice versa, then re-indexes backlinks for both entries.

        Args:
            source_id: ID of the first entry.
            target_id: ID of the second entry.
        """
        from analecta.markdown.frontmatter import update_linked

        source = self.get_entry(source_id)
        target = self.get_entry(target_id)
        if source is None or target is None:
            return

        for entry, other_title in ((source, target.title), (target, source.title)):
            fp = Path(entry.file_path)
            if not fp.exists():
                continue
            md = fp.read_text(encoding="utf-8")
            updated = update_linked(md, add=other_title)
            if updated != md:
                fp.write_text(updated, encoding="utf-8")

        self.index_backlinks(source_id)
        self.index_backlinks(target_id)

    @_synchronized
    def remove_link(self, source_id: int, target_id: int) -> None:
        """Remove the bidirectional explicit link between two entries.

        Removes the target's title from the source's frontmatter ``linked``
        field and vice versa, then re-indexes backlinks for both entries.

        Args:
            source_id: ID of the first entry.
            target_id: ID of the second entry.
        """
        from analecta.markdown.frontmatter import update_linked

        source = self.get_entry(source_id)
        target = self.get_entry(target_id)
        if source is None or target is None:
            return

        for entry, other_title in ((source, target.title), (target, source.title)):
            fp = Path(entry.file_path)
            if not fp.exists():
                continue
            md = fp.read_text(encoding="utf-8")
            updated = update_linked(md, remove=other_title)
            if updated != md:
                fp.write_text(updated, encoding="utf-8")

        self.index_backlinks(source_id)
        self.index_backlinks(target_id)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _fts_prefix_query(raw: str) -> str:
    """Build a safe FTS5 prefix-match expression from a raw user query.

    Strips FTS5 special characters, splits on whitespace, and appends ``*``
    to each term so partial words match (e.g. ``"Rolld"`` finds
    ``"Rolldown…"``).  Returns an empty string when no terms remain, which
    the caller interprets as "return no results".

    Args:
        raw: Unsanitized user input.

    Returns:
        FTS5-safe query string, or ``""`` if nothing remains after sanitization.
    """
    terms = _re.sub(r"[^\w\s]", " ", raw, flags=_re.UNICODE).split()
    return " ".join(f"{t.lower()}*" for t in terms)


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
