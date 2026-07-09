import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from analecta.storage.index import EntryRecord, InvalidTagNameError, VaultIndex
from analecta.storage.vault import VaultManager, _slugify

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _entry(**kwargs) -> EntryRecord:
    defaults: dict[str, Any] = dict(
        title="Test Entry",
        url="https://example.com/test",
        file_path="/vault/pages/2026-04-30-test-entry.md",
        source_type="article",
        created_at=_now(),
        updated_at=_now(),
    )
    defaults.update(kwargs)
    return EntryRecord(**defaults)


@pytest.fixture
def index(tmp_path: Path):
    db = VaultIndex(tmp_path / "vault.db")
    yield db
    db.close()


@pytest.fixture
def vault(tmp_path: Path) -> VaultManager:
    return VaultManager(tmp_path / "vault")


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert _slugify("Hello World") == "hello-world"


def test_slugify_unicode():
    assert _slugify("Héllo Wörld") == "hello-world"


def test_slugify_strips_special_chars():
    assert _slugify("C++ is great!") == "c-is-great"


def test_slugify_collapses_hyphens():
    assert _slugify("a  --  b") == "a-b"


def test_slugify_max_len():
    assert len(_slugify("a" * 200)) <= 60


def test_slugify_empty_returns_empty():
    assert _slugify("") == ""


# ---------------------------------------------------------------------------
# VaultManager
# ---------------------------------------------------------------------------


def test_vault_ensure_dirs_creates_tree(vault: VaultManager):
    vault.ensure_dirs()
    assert vault.pages_path.is_dir()
    assert vault.assets_path.is_dir()


def test_vault_page_path_format(vault: VaultManager):
    dt = datetime(2026, 4, 30, tzinfo=UTC)
    assert vault.page_path("My Article", dt).name == "2026-04-30-my-article.md"


def test_vault_page_path_fallback_slug(vault: VaultManager):
    dt = datetime(2026, 4, 30, tzinfo=UTC)
    assert vault.page_path("!!!", dt).name == "2026-04-30-entry.md"


def test_vault_write_page_creates_file(vault: VaultManager):
    dt = datetime(2026, 4, 30, tzinfo=UTC)
    path = vault.write_page("# Content", "My Article", dt)
    assert path.exists()
    assert path.read_text() == "# Content"


def test_vault_asset_dir(vault: VaultManager):
    d = vault.asset_dir("my-article")
    assert d == vault.assets_path / "my-article"


# ---------------------------------------------------------------------------
# VaultIndex — migrations
# ---------------------------------------------------------------------------


def test_schema_migrations_table_exists(index: VaultIndex):
    row = index._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    assert row is not None


def test_migration_001_applied(index: VaultIndex):
    row = index._conn.execute(
        "SELECT version FROM schema_migrations WHERE version = '001_init.sql'"
    ).fetchone()
    assert row is not None


def test_entry_tags_tag_id_index_exists(index: VaultIndex):
    row = index._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_entry_tags_tag_id'"
    ).fetchone()
    assert row is not None


def test_entries_table_exists(index: VaultIndex):
    row = index._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='entries'"
    ).fetchone()
    assert row is not None


def test_fts_table_exists(index: VaultIndex):
    row = index._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='entries_fts'"
    ).fetchone()
    assert row is not None


def test_context_manager(tmp_path: Path):
    with VaultIndex(tmp_path / "vault.db") as idx:
        assert idx.list_entries() == []


def _seed_pre_008_vault(db_path: Path) -> None:
    """Build a vault at ``db_path`` on the schema as it existed just before
    008_tags_normalized.sql — i.e. with a real, pre-fix case-duplicate
    tag ("Python" / "python" as two separate rows) already on disk.

    Applies the real 001-007 migration files (so the schema is exactly
    what production vaults have), marks them plus the 007 backlinks
    bootstrap as already-applied, then inserts entries/tags/entry_tags
    directly — bypassing VaultIndex entirely, since post-008 the app
    layer itself refuses to create a case-duplicate.
    """
    import importlib.resources

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE schema_migrations"
        " (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    migrations_dir = importlib.resources.files("analecta") / "migrations"
    applied: list[str] = []
    for resource in sorted(
        (r for r in migrations_dir.iterdir() if r.name.endswith(".sql")),
        key=lambda r: r.name,
    ):
        if resource.name >= "008":
            continue
        conn.executescript(resource.read_text(encoding="utf-8"))
        applied.append(resource.name)
    applied.append("py:008_backlinks_bootstrap")
    now = _now()
    for version in applied:
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, now),
        )

    entry_ids = []
    for i in range(4):
        cur = conn.execute(
            """
            INSERT INTO entries
                (title, url, file_path, source_type, created_at, updated_at, tags_json)
            VALUES (?, ?, ?, 'article', ?, ?, '[]')
            """,
            (f"Entry {i}", f"https://example.com/{i}", f"/vault/{i}.md", now, now),
        )
        entry_ids.append(cur.lastrowid)

    conn.execute("INSERT INTO tags (name, count) VALUES ('Python', 0)")
    python_id = conn.execute("SELECT id FROM tags WHERE name = 'Python'").fetchone()[0]
    conn.execute("INSERT INTO tags (name, count) VALUES ('python', 0)")
    python_lower_id = conn.execute(
        "SELECT id FROM tags WHERE name = 'python'"
    ).fetchone()[0]

    # 3 entries carry the tag as "Python" (more real usage), 1 as "python".
    for eid in entry_ids[:3]:
        conn.execute(
            "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
            (eid, python_id),
        )
        conn.execute(
            "UPDATE entries SET tags_json = ? WHERE id = ?",
            (json.dumps(["Python"]), eid),
        )
    conn.execute(
        "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
        (entry_ids[3], python_lower_id),
    )
    conn.execute(
        "UPDATE entries SET tags_json = ? WHERE id = ?",
        (json.dumps(["python"]), entry_ids[3]),
    )
    conn.commit()
    conn.close()


def test_bootstrap_tag_normalization_is_noop_on_fresh_vault(tmp_path: Path):
    with VaultIndex(tmp_path / "vault.db") as index:
        assert index.list_tags() == []
        row = index._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master"
            " WHERE type='index' AND name='idx_tags_normalized'"
        ).fetchone()
        assert row[0] == 1


def test_bootstrap_tag_normalization_merges_case_duplicates(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    _seed_pre_008_vault(db_path)

    with VaultIndex(db_path) as index:
        pairs = dict(index.list_tags())
        # Merged into one entry, under the higher-usage display casing.
        assert pairs.get("Python") == 4
        assert "python" not in pairs

        row_count = index._conn.execute(
            "SELECT COUNT(*) FROM tags WHERE normalized = 'python'"
        ).fetchone()[0]
        assert row_count == 1

        # The entry that carried the lowercase variant is rewritten to the
        # canonical casing, deduplicated (not ["Python", "Python"]).
        merged_entry = index.get_entry(4)
        assert merged_entry is not None
        assert json.loads(merged_entry.tags_json) == ["Python"]

        # No stray entry_tags rows point at a now-deleted duplicate row.
        orphaned = index._conn.execute(
            """
            SELECT COUNT(*) FROM entry_tags et
            LEFT JOIN tags t ON t.id = et.tag_id
            WHERE t.id IS NULL
            """
        ).fetchone()[0]
        assert orphaned == 0


def test_bootstrap_tag_normalization_dedupes_entry_carrying_both_variants(
    tmp_path: Path,
):
    db_path = tmp_path / "legacy.db"
    _seed_pre_008_vault(db_path)

    # Directly add the case variant onto an entry that already carries the
    # canonical one — simulates a vault where one entry was tagged both
    # "Python" and "python" before the identity was unified.
    conn = sqlite3.connect(str(db_path))
    python_lower_id = conn.execute(
        "SELECT id FROM tags WHERE name = 'python'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO entry_tags (entry_id, tag_id) VALUES (1, ?)", (python_lower_id,)
    )
    conn.execute(
        "UPDATE entries SET tags_json = ? WHERE id = 1",
        (json.dumps(["Python", "python"]),),
    )
    conn.commit()
    conn.close()

    with VaultIndex(db_path) as index:
        entry = index.get_entry(1)
        assert entry is not None
        assert json.loads(entry.tags_json) == ["Python"]


# ---------------------------------------------------------------------------
# VaultIndex — CRUD
# ---------------------------------------------------------------------------


def test_add_entry_returns_int(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    assert isinstance(entry_id, int)
    assert entry_id >= 1


def test_get_entry_roundtrip(index: VaultIndex):
    entry_id = index.add_entry(_entry(title="Roundtrip", url="https://a.com/rt"))
    got = index.get_entry(entry_id)
    assert got is not None
    assert got.title == "Roundtrip"
    assert got.status == "unread"
    assert got.id == entry_id


def test_get_entry_missing_returns_none(index: VaultIndex):
    assert index.get_entry(9999) is None


def test_duplicate_url_raises_integrity_error(index: VaultIndex):
    index.add_entry(_entry(url="https://dup.com"))
    with pytest.raises(sqlite3.IntegrityError):
        index.add_entry(_entry(url="https://dup.com"))


def test_update_status(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_status(entry_id, "read")
    entry = index.get_entry(entry_id)
    assert entry is not None
    assert entry.status == "read"


def test_soft_delete_sets_deleted_status(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.soft_delete(entry_id)
    entry = index.get_entry(entry_id)
    assert entry is not None
    assert entry.status == "deleted"


def test_update_tags_sets_json(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    entry = index.get_entry(entry_id)
    assert entry is not None
    assert json.loads(entry.tags_json) == ["python", "sqlite"]


def _live_tag_count(index: VaultIndex, name: str) -> int:
    row = index._conn.execute(
        """
        SELECT COUNT(*) FROM entry_tags et
        JOIN tags t ON t.id = et.tag_id
        WHERE t.name = ?
        """,
        (name,),
    ).fetchone()
    return row[0]


def test_update_tags_syncs_tags_table(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    assert _live_tag_count(index, "python") == 1


def test_update_tags_replaces_previous(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    index.update_tags(entry_id, ["python"])
    assert _live_tag_count(index, "sqlite") == 0


def test_get_entries_by_ids_empty(index: VaultIndex):
    assert index.get_entries_by_ids([]) == []


def test_get_entries_by_ids_returns_matching(index: VaultIndex):
    e1 = index.add_entry(_entry(url="https://a.com"))
    e2 = index.add_entry(_entry(url="https://b.com"))
    index.add_entry(_entry(url="https://c.com"))
    records = index.get_entries_by_ids([e1, e2])
    assert {r.id for r in records} == {e1, e2}


def test_get_entries_by_ids_omits_missing(index: VaultIndex):
    e1 = index.add_entry(_entry(url="https://a.com"))
    records = index.get_entries_by_ids([e1, 9999])
    assert [r.id for r in records] == [e1]


def test_list_entries_all(index: VaultIndex):
    index.add_entry(_entry(url="https://a.com"))
    index.add_entry(_entry(url="https://b.com"))
    assert len(index.list_entries()) == 2


def test_list_entries_by_status(index: VaultIndex):
    id1 = index.add_entry(_entry(url="https://a.com"))
    index.add_entry(_entry(url="https://b.com"))
    index.update_status(id1, "read")
    assert len(index.list_entries(status="read")) == 1
    assert len(index.list_entries(status="unread")) == 1


def test_get_all_titles_empty(index: VaultIndex):
    assert index.get_all_titles() == []


def test_get_all_titles_returns_id_and_title(index: VaultIndex):
    id1 = index.add_entry(_entry(title="Alpha", url="https://a.com"))
    id2 = index.add_entry(_entry(title="Beta", url="https://b.com"))
    assert index.get_all_titles() == [(id1, "Alpha"), (id2, "Beta")]


# ---------------------------------------------------------------------------
# VaultIndex — FTS5 search
# ---------------------------------------------------------------------------


def test_search_by_title(index: VaultIndex):
    index.add_entry(_entry(title="asyncio coroutines", url="https://a.com"))
    index.add_entry(_entry(title="SQLite performance", url="https://b.com"))
    results = index.search("asyncio")
    assert len(results) == 1
    assert results[0].title == "asyncio coroutines"


def test_search_by_body_after_fts_update(index: VaultIndex):
    entry_id = index.add_entry(_entry(title="Some Article", url="https://a.com"))
    index.update_fts_content(
        entry_id, "Some Article", "coroutines are fundamental to asyncio"
    )
    results = index.search("coroutines")
    assert len(results) == 1


def test_search_no_results(index: VaultIndex):
    index.add_entry(_entry())
    assert index.search("xyzzy_nonexistent") == []


def test_search_prefix_match(index: VaultIndex):
    index.add_entry(
        _entry(title="Rolldown paused Rust integration", url="https://a.com")
    )
    index.add_entry(_entry(title="React Compiler update", url="https://b.com"))
    results = index.search("Rolld")
    assert len(results) == 1
    assert results[0].title == "Rolldown paused Rust integration"


def test_search_prefix_partial_word(index: VaultIndex):
    index.add_entry(_entry(title="socket security scan", url="https://a.com"))
    assert len(index.search("Socke")) == 1
    assert len(index.search("sock")) == 1


def test_search_multiterm_prefix(index: VaultIndex):
    index.add_entry(_entry(title="React Compiler deep dive", url="https://a.com"))
    index.add_entry(_entry(title="Python asyncio guide", url="https://b.com"))
    results = index.search("React Comp")
    assert len(results) == 1
    assert results[0].title == "React Compiler deep dive"


def test_search_special_chars_sanitized(index: VaultIndex):
    index.add_entry(_entry(title="hello world article", url="https://a.com"))
    # Special FTS5 chars should be stripped, not cause an error
    results = index.search('hello"world')
    assert isinstance(results, list)


def test_search_fts_keyword_as_term(index: VaultIndex):
    # "AND" was previously an invalid FTS5 query; now sanitized to "AND*"
    index.add_entry(_entry(title="Android development", url="https://a.com"))
    results = index.search("AND")
    assert isinstance(results, list)
    assert any(r.title == "Android development" for r in results)


def test_search_only_special_chars_returns_empty(index: VaultIndex):
    index.add_entry(_entry())
    assert index.search("!!!") == []


# ---------------------------------------------------------------------------
# VaultIndex — tag management (create / rename / delete)
# ---------------------------------------------------------------------------


def test_create_tag(index: VaultIndex):
    index.create_tag("python")
    assert any(name == "python" for name, _ in index.list_tags())


def test_create_tag_returns_new_name_and_zero_count(index: VaultIndex):
    assert index.create_tag("newtag") == ("newtag", 0)


def test_create_tag_case_insensitive_returns_existing_canonical(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["Python"])
    # Creating "PYTHON" must no-op and report the existing identity's
    # canonical casing and true count, not a fabricated (name, 0).
    assert index.create_tag("PYTHON") == ("Python", 1)


def test_create_tag_unifies_with_existing_hashtag_count(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Great article about #python.\n", encoding="utf-8")
    content_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(content_id)

    # "python" has no structural tags-table row yet, only a content
    # hashtag — creating it structurally must report the pre-existing
    # hashtag occurrence in its count, not 0.
    assert index.create_tag("python") == ("python", 1)


def test_create_tag_invalid_name_raises(index: VaultIndex):
    with pytest.raises(InvalidTagNameError, match="Cannot create"):
        index.create_tag("C++")
    names = [n for n, _ in index.list_tags()]
    assert "C++" not in names


@pytest.mark.parametrize("name", ["my tag", "`x`", "🎉", "", "   "])
def test_create_tag_invalid_names_raise(index: VaultIndex, name: str):
    with pytest.raises(InvalidTagNameError):
        index.create_tag(name)


def test_create_tag_valid_accented_name_succeeds(index: VaultIndex):
    assert index.create_tag("café") == ("café", 0)


def test_create_tag_idempotent(index: VaultIndex):
    index.create_tag("python")
    index.create_tag("python")  # should not raise
    assert sum(1 for name, _ in index.list_tags() if name == "python") == 1


def test_create_tag_case_insensitive_noop(index: VaultIndex):
    index.create_tag("Python")
    index.create_tag("PYTHON")  # same identity — no new row, no rename
    names = [n for n, _ in index.list_tags()]
    assert names == ["Python"]
    row_count = index._conn.execute(
        "SELECT COUNT(*) FROM tags WHERE normalized = 'python'"
    ).fetchone()[0]
    assert row_count == 1


def test_update_tags_case_insensitive_reuses_existing_row(index: VaultIndex):
    e1 = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(e1, ["Python"])
    e2 = index.add_entry(_entry(url="https://b.com"))
    index.update_tags(e2, ["PYTHON"])  # same identity as "Python"

    row_count = index._conn.execute(
        "SELECT COUNT(*) FROM tags WHERE normalized = 'python'"
    ).fetchone()[0]
    assert row_count == 1

    # Display casing stays the first-seen one; both entries count toward it.
    pairs = dict(index.list_tags())
    assert pairs == {"Python": 2}


def test_rename_tag_updates_table(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python"])
    index.rename_tag("python", "py")
    names = [n for n, _ in index.list_tags()]
    assert "py" in names
    assert "python" not in names


def test_rename_tag_updates_entries_json(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    index.rename_tag("python", "py")
    import json as _json

    entry = index.get_entry(entry_id)
    assert entry is not None
    tags = _json.loads(entry.tags_json)
    assert "py" in tags
    assert "python" not in tags
    assert "sqlite" in tags


def test_rename_tag_conflict_raises(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    with pytest.raises(ValueError, match="already exists"):
        index.rename_tag("python", "sqlite")


def test_rename_tag_conflict_case_insensitive(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "SQLite"])
    with pytest.raises(ValueError, match="already exists"):
        index.rename_tag("python", "sqlite")  # differs only by case from "SQLite"


def test_rename_tag_into_content_only_identity_allowed(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #rust.\n", encoding="utf-8")
    content_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(content_id)

    structural_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(structural_id, ["ferrous"])

    # "rust" only exists today as a content hashtag (no tags-table row) —
    # renaming a structural tag into that identity must not raise.
    index.rename_tag("ferrous", "rust")

    ids = index.get_entry_ids_by_tag("rust")
    assert set(ids) == {structural_id, content_id}


def test_rename_tag_content_only_merges_into_existing_structural_tag(
    index: VaultIndex, tmp_path: Path
):
    """Mirror of test_rename_tag_into_content_only_identity_allowed.

    A content-only tag ("python", no structural row) renamed into an
    identity that already exists structurally ("rust") merges rather than
    conflicting — unlike two structural tags colliding (blocked with a
    ValueError), there's no second entry_tags/tags_json row to reconcile
    here, so this is the same "allowed unification" the structural ->
    content-only direction already gets, just mirrored.
    """
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    content_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(content_id)

    structural_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(structural_id, ["rust"])

    index.rename_tag("python", "rust")  # must not raise

    assert "#rust" in src_file.read_text(encoding="utf-8")
    ids = index.get_entry_ids_by_tag("rust")
    assert set(ids) == {structural_id, content_id}


# ---------------------------------------------------------------------------
# rename_tag — merging two structural tags (merge=True)
# ---------------------------------------------------------------------------


def test_rename_tag_merge_blocked_without_flag(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["celeste"])
    other_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(other_id, ["Azul"])

    with pytest.raises(ValueError, match="already exists"):
        index.rename_tag("celeste", "azul")  # merge defaults to False


def test_rename_tag_merge_migrates_body_hashtag_with_destination_casing(
    index: VaultIndex, tmp_path: Path
):
    """The exact scenario asked about: a mixed-case body hashtag survives a
    merge into a *preexisting* destination tag, and adopts the destination's
    canonical casing rather than whatever was typed in the rename box.
    """
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("The sky is #Celeste today.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["celeste"])
    index.index_backlinks(entry_id)

    dest_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(dest_id, ["Azul"])  # preexisting, mixed-case display name

    result = index.rename_tag("celeste", "azul", merge=True)

    # Destination's preexisting casing wins in the body text too — not the
    # "azul" that was actually typed into the rename input.
    assert "#Azul" in src_file.read_text(encoding="utf-8")
    assert "#Celeste" not in src_file.read_text(encoding="utf-8")
    assert result == ("Azul", 2)
    assert index.get_entry_ids_by_tag("celeste") == []
    assert set(index.get_entry_ids_by_tag("Azul")) == {entry_id, dest_id}


def test_rename_tag_merge_reassigns_entry_tags(index: VaultIndex):
    a_id = index.add_entry(_entry())
    index.update_tags(a_id, ["celeste"])
    b_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(b_id, ["azul"])

    result = index.rename_tag("celeste", "azul", merge=True)

    assert result == ("azul", 2)
    names = [n for n, _ in index.list_tags()]
    assert "celeste" not in names
    assert set(index.get_entry_ids_by_tag("azul")) == {a_id, b_id}
    entry_a = index.get_entry(a_id)
    assert entry_a is not None
    assert json.loads(entry_a.tags_json) == ["azul"]


def test_rename_tag_merge_dedupes_entry_with_both_tags(index: VaultIndex):
    # entry already carries both "celeste" and "azul" structurally.
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["celeste", "azul"])

    index.rename_tag("celeste", "azul", merge=True)

    entry = index.get_entry(entry_id)
    assert entry is not None
    assert json.loads(entry.tags_json) == ["azul"]
    # entry_tags must not have a duplicate/dangling row either.
    tag_id = index._conn.execute(
        "SELECT id FROM tags WHERE normalized = 'azul'"
    ).fetchone()["id"]
    rows = index._conn.execute(
        "SELECT COUNT(*) FROM entry_tags WHERE entry_id = ? AND tag_id = ?",
        (entry_id, tag_id),
    ).fetchone()
    assert rows[0] == 1


def test_rename_tag_merge_tags_json_uses_destination_casing(index: VaultIndex):
    """Discriminating case for the tags_json write, not just the return tuple.

    Every other merge test above types the same casing as the destination
    (or asserts membership via get_entry_ids_by_tag, which resolves through
    entry_tags, not tags_json) — none of them would catch tags_json ending
    up with the literally-typed new_name instead of the destination's
    preexisting display casing. This one types a different casing than the
    destination on purpose.
    """
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["celeste"])
    dest_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(dest_id, ["Azul"])  # preexisting mixed-case display name

    index.rename_tag("celeste", "azul", merge=True)  # typed lowercase

    entry = index.get_entry(entry_id)
    assert entry is not None
    assert json.loads(entry.tags_json) == ["Azul"]


def test_rename_tag_nonexistent_noop(index: VaultIndex):
    index.rename_tag("nonexistent", "other")  # should not raise


def test_rename_tag_nonexistent_returns_none(index: VaultIndex):
    assert index.rename_tag("nonexistent", "other") is None


def test_rename_tag_returns_new_name_and_count(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python"])
    assert index.rename_tag("python", "py") == ("py", 1)


def test_rename_tag_case_insensitive_old_name_lookup(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["Python"])
    # old_name lookup must match "Python" case-insensitively.
    assert index.rename_tag("PYTHON", "py") == ("py", 1)
    names = [n for n, _ in index.list_tags()]
    assert "Python" not in names
    assert "py" in names

    # tags_json holds "Python" (display casing), not "PYTHON" (the arg
    # passed to rename_tag) — the rewrite must match it by casefold too.
    entry = index.get_entry(entry_id)
    assert entry is not None
    tags = json.loads(entry.tags_json)
    assert "py" in tags
    assert "Python" not in tags


# ---------------------------------------------------------------------------
# rename_tag — migrates surviving body-text #hashtag occurrences
# ---------------------------------------------------------------------------


def test_rename_tag_migrates_body_hashtag_on_same_entry(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["python"])
    index.index_backlinks(entry_id)

    index.rename_tag("python", "py")

    assert "#py" in src_file.read_text(encoding="utf-8")
    assert "#python" not in src_file.read_text(encoding="utf-8")
    names = [n for n, _ in index.list_tags()]
    assert "py" in names
    assert "python" not in names


def test_rename_tag_closes_the_split_across_entries(index: VaultIndex, tmp_path: Path):
    """The core regression: rename must not let a tag split into two identities.

    Entry A carries the tag structurally; entry B only mentions it as a
    literal ``#python`` in its body. Before this fix, renaming via A's
    structural row left B's body text pointing at the old (lowercase)
    identity, so it would reappear in list_tags() as a separate tag.
    """
    vault = tmp_path / "pages"
    vault.mkdir()
    body_file = vault / "article.md"
    body_file.write_text("Filed under #python.\n", encoding="utf-8")

    structural_id = index.add_entry(_entry())
    index.update_tags(structural_id, ["python"])

    content_id = index.add_entry(_entry(url="https://b.com", file_path=str(body_file)))
    index.index_backlinks(content_id)

    result = index.rename_tag("python", "py")

    assert "#py" in body_file.read_text(encoding="utf-8")
    assert index.get_entry_ids_by_tag("python") == []
    assert set(index.get_entry_ids_by_tag("py")) == {structural_id, content_id}
    # The returned count reflects the migration, not just the structural
    # side — computed after both the structural update and the body rewrite.
    assert result == ("py", 2)


def test_rename_tag_content_only_migrates_body_text(index: VaultIndex, tmp_path: Path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    # No structural row exists at all for "python".
    row = index._conn.execute(
        "SELECT id FROM tags WHERE normalized = 'python'"
    ).fetchone()
    assert row is None

    result = index.rename_tag("python", "py")

    assert "#py" in src_file.read_text(encoding="utf-8")
    assert result == ("py", 1)
    pairs = dict(index.list_tags())
    assert pairs.get("py") == 1
    assert "python" not in pairs


def test_rename_tag_symbol_bearing_old_name_does_not_touch_unrelated_hashtag(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Written in #c.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    structural_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(structural_id, ["C++"])

    index.rename_tag("C++", "cpp")

    # The unrelated "#c" content hashtag in article.md must survive untouched.
    assert src_file.read_text(encoding="utf-8") == "Written in #c.\n"
    assert index.get_entry_ids_by_tag("c") == [entry_id]
    names = [n for n, _ in index.list_tags()]
    assert "cpp" in names
    assert "C++" not in names


def test_rename_tag_no_body_occurrence_no_file_write(index: VaultIndex, tmp_path: Path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("No hashtags here.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["python"])
    index.index_backlinks(entry_id)

    index.rename_tag("python", "py")

    assert src_file.read_text(encoding="utf-8") == "No hashtags here.\n"


def test_rename_tag_clears_stale_backlink_cache(index: VaultIndex, tmp_path: Path):
    """A ghost tag entry: cache says the body has the hashtag, body doesn't.

    Simulates an entry that was edited outside the app (e.g. the hashtag
    was removed manually) so backlink_refs no longer matches the file on
    disk. rename_tag's own rewrite finds nothing to change, but it must
    still reindex the entry so the stale row doesn't survive forever.
    """
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("No hashtags here anymore.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index._conn.execute(
        "INSERT INTO backlink_refs (source_id, target_text, is_hashtag,"
        " pre, highlight, post) VALUES (?, 'ghost', 1, '', '#ghost', '')",
        (entry_id,),
    )
    index._conn.commit()
    assert index.get_body_hashtag_entry_ids("ghost") == [entry_id]

    index.rename_tag("ghost", "renamed")

    assert index.get_body_hashtag_entry_ids("ghost") == []
    names = [n for n, _ in index.list_tags()]
    assert "ghost" not in names


def test_rename_tag_invalid_new_name_with_body_occurrences_raises(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["python"])
    index.index_backlinks(entry_id)

    with pytest.raises(InvalidTagNameError, match="Cannot rename"):
        index.rename_tag("python", "C++")

    # Nothing was written — neither the structural row nor the body text.
    assert src_file.read_text(encoding="utf-8") == "Filed under #python.\n"
    names = [n for n, _ in index.list_tags()]
    assert "python" in names
    assert "C++" not in names


def test_rename_tag_accented_new_name_with_body_occurrences_succeeds(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["python"])
    index.index_backlinks(entry_id)

    result = index.rename_tag("python", "programación")

    assert result == ("programación", 1)
    assert src_file.read_text(encoding="utf-8") == "Filed under #programación.\n"
    tags = index.list_tags()
    # The renamed structural tag and its just-migrated literal #hashtag
    # occurrence must land on one identity, not fragment into a phantom
    # ASCII-folded row alongside the accented one.
    assert tags == [("programación", 1)]


def test_accented_structural_tag_and_content_hashtag_unify(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Body mentions #café here.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["café"])
    index.index_backlinks(entry_id)

    # One entry carrying "café" both structurally and as a content hashtag
    # must appear as a single unified tag, not split into "café" (structural,
    # casefold identity) and "cafe" (content, accent-stripped identity).
    assert index.list_tags() == [("café", 1)]
    assert index.get_entry_ids_by_tag("café") == [entry_id]


def test_rename_tag_invalid_new_name_without_body_occurrences_raises(
    index: VaultIndex,
):
    # No body hashtag occurrence exists at all, but validation is now
    # unconditional — a structural-only rename into a symbol-bearing name
    # is rejected too, so every tag stays writable as a literal #hashtag.
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python"])

    with pytest.raises(InvalidTagNameError, match="Cannot rename"):
        index.rename_tag("python", "C++")

    names = [n for n, _ in index.list_tags()]
    assert "python" in names
    assert "C++" not in names


def test_delete_tag_removes_from_table(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python"])
    index.delete_tag("python")
    assert not any(name == "python" for name, _ in index.list_tags())


def test_delete_tag_removes_from_entries_json(index: VaultIndex):
    import json as _json

    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    index.delete_tag("python")
    entry = index.get_entry(entry_id)
    assert entry is not None
    tags = _json.loads(entry.tags_json)
    assert "python" not in tags
    assert "sqlite" in tags


def test_delete_tag_nonexistent_noop(index: VaultIndex):
    index.delete_tag("nonexistent")  # should not raise


def test_delete_tag_case_insensitive_lookup(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["Python"])
    # name lookup must match "Python" case-insensitively.
    index.delete_tag("PYTHON")
    assert not any(name == "Python" for name, _ in index.list_tags())

    # tags_json holds "Python" (display casing), not "PYTHON" (the arg
    # passed to delete_tag) — the rewrite must match it by casefold too,
    # otherwise the stale string resurrects the tag on the next update_tags.
    entry = index.get_entry(entry_id)
    assert entry is not None
    tags = json.loads(entry.tags_json)
    assert "Python" not in tags


# ---------------------------------------------------------------------------
# get_body_hashtag_entry_ids — content-hashtag-only lookup for delete warnings
# ---------------------------------------------------------------------------


def test_get_body_hashtag_entry_ids_finds_content_occurrence(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    assert index.get_body_hashtag_entry_ids("python") == [entry_id]


def test_get_body_hashtag_entry_ids_excludes_structural_only(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python"])

    # No markdown body hashtag at all — structural-only doesn't count.
    assert index.get_body_hashtag_entry_ids("python") == []


def test_get_body_hashtag_entry_ids_deduplicated_and_sorted(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    ids = []
    for i in range(3):
        src_file = vault / f"article-{i}.md"
        src_file.write_text("#python #python again\n", encoding="utf-8")
        entry_id = index.add_entry(
            _entry(url=f"https://example.com/{i}", file_path=str(src_file))
        )
        index.index_backlinks(entry_id)
        ids.append(entry_id)

    assert index.get_body_hashtag_entry_ids("python") == sorted(ids)


def test_get_body_hashtag_entry_ids_symbol_bearing_name_returns_empty(
    index: VaultIndex, tmp_path: Path
):
    """ "C++" has no valid hashtag form — must never spuriously match "#c"."""
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Written in #c.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    assert index.get_body_hashtag_entry_ids("C++") == []
    # "c" itself (a valid hashtag form) still finds it.
    assert index.get_body_hashtag_entry_ids("c") == [entry_id]


# ---------------------------------------------------------------------------
# delete_tag — neutralizes surviving body-text #hashtag occurrences
# ---------------------------------------------------------------------------


def test_delete_tag_wraps_surviving_body_hashtag(index: VaultIndex, tmp_path: Path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["python"])
    index.index_backlinks(entry_id)

    index.delete_tag("python")

    assert "`#python`" in src_file.read_text(encoding="utf-8")
    # Structural row gone AND body text no longer resurrects it.
    assert not any(name == "python" for name, _ in index.list_tags())


def test_delete_tag_content_only_tag_fully_removed(index: VaultIndex, tmp_path: Path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    # No structural row exists at all for "python".
    row = index._conn.execute(
        "SELECT id FROM tags WHERE normalized = 'python'"
    ).fetchone()
    assert row is None

    index.delete_tag("python")

    assert "`#python`" in src_file.read_text(encoding="utf-8")
    assert not any(name == "python" for name, _ in index.list_tags())


def test_delete_tag_symbol_bearing_name_does_not_touch_unrelated_hashtag(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Written in #c.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    structural_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(structural_id, ["C++"])

    index.delete_tag("C++")

    # The unrelated "#c" content hashtag in article.md must survive untouched.
    assert src_file.read_text(encoding="utf-8") == "Written in #c.\n"
    assert index.get_entry_ids_by_tag("c") == [entry_id]
    assert not any(name == "C++" for name, _ in index.list_tags())


def test_delete_tag_no_body_occurrence_no_file_write(index: VaultIndex, tmp_path: Path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("No hashtags here.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["python"])
    index.index_backlinks(entry_id)

    index.delete_tag("python")

    assert src_file.read_text(encoding="utf-8") == "No hashtags here.\n"


def test_delete_tag_clears_stale_backlink_cache(index: VaultIndex, tmp_path: Path):
    """Mirrors test_rename_tag_clears_stale_backlink_cache for delete_tag.

    A backlink_refs row can outlive the literal hashtag it was indexed
    from (e.g. the file was edited outside the app). delete_tag's own
    neutralize pass finds nothing to wrap, but must still reindex so the
    ghost tag doesn't keep showing up in the sidebar's tag list.
    """
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("No hashtags here anymore.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index._conn.execute(
        "INSERT INTO backlink_refs (source_id, target_text, is_hashtag,"
        " pre, highlight, post) VALUES (?, 'ghost', 1, '', '#ghost', '')",
        (entry_id,),
    )
    index._conn.commit()
    assert index.get_body_hashtag_entry_ids("ghost") == [entry_id]

    index.delete_tag("ghost")

    assert index.get_body_hashtag_entry_ids("ghost") == []
    names = [n for n, _ in index.list_tags()]
    assert "ghost" not in names


# ---------------------------------------------------------------------------
# list_tags — unions structural tags with content-only hashtags
# ---------------------------------------------------------------------------


def test_list_tags_content_only_hashtag_appears(index: VaultIndex, tmp_path: Path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Great article about #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    # No structural "python" tag exists — the content hashtag still shows up.
    pairs = dict(index.list_tags())
    assert pairs["python"] == 1


def test_list_tags_content_hashtag_count_multiple_entries(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    for i in range(2):
        src_file = vault / f"article-{i}.md"
        src_file.write_text("Filed under #python.\n", encoding="utf-8")
        entry_id = index.add_entry(
            _entry(url=f"https://example.com/{i}", file_path=str(src_file))
        )
        index.index_backlinks(entry_id)

    pairs = dict(index.list_tags())
    assert pairs["python"] == 2


def test_list_tags_unions_structural_and_content_same_name(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")

    structural_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(structural_id, ["python"])

    content_id = index.add_entry(_entry(url="https://b.com", file_path=str(src_file)))
    index.index_backlinks(content_id)

    # Same union semantics as get_entry_ids_by_tag: an entry with the tag
    # only as a content hashtag still counts, alongside the structural one.
    pairs = dict(index.list_tags())
    assert pairs["python"] == 2


def test_list_tags_content_hashtag_case_variants_count_as_one_tag(
    index: VaultIndex, tmp_path: Path
):
    """#Python in one article and #python in another are the same tag.

    Direct regression guard for the literal requirement: the casing typed
    in the article body is never rewritten (each file keeps what its
    author/site originally wrote), but both occurrences must contribute
    to the same tag identity and count.
    """
    vault = tmp_path / "pages"
    vault.mkdir()
    file_a = vault / "a.md"
    file_a.write_text("Great article about #Python.\n", encoding="utf-8")
    file_b = vault / "b.md"
    file_b.write_text("Also using #python here.\n", encoding="utf-8")
    file_c = vault / "c.md"
    file_c.write_text("And #PYTHON again.\n", encoding="utf-8")

    id_a = index.add_entry(_entry(url="https://a.com", file_path=str(file_a)))
    index.index_backlinks(id_a)
    id_b = index.add_entry(_entry(url="https://b.com", file_path=str(file_b)))
    index.index_backlinks(id_b)
    id_c = index.add_entry(_entry(url="https://c.com", file_path=str(file_c)))
    index.index_backlinks(id_c)

    # One tag, not three — count reflects all three articles.
    pairs = dict(index.list_tags())
    assert sum(1 for name in pairs if name.casefold() == "python") == 1
    assert pairs["python"] == 3
    assert set(index.get_entry_ids_by_tag("python")) == {id_a, id_b, id_c}

    # The raw body text is untouched — each file keeps its original casing.
    assert "#Python" in file_a.read_text(encoding="utf-8")
    assert "#python" in file_b.read_text(encoding="utf-8")
    assert "#PYTHON" in file_c.read_text(encoding="utf-8")


def test_list_tags_dedupes_entry_with_both_structural_and_content_tag(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")

    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.update_tags(entry_id, ["python"])
    index.index_backlinks(entry_id)

    # Same entry carries "python" both structurally and as a content
    # hashtag — counted once, not twice.
    pairs = dict(index.list_tags())
    assert pairs["python"] == 1


def test_list_tags_sorted_alphabetically_case_insensitive(index: VaultIndex):
    index.create_tag("zebra")
    index.create_tag("Apple")
    index.create_tag("banana")

    names = [name for name, _ in index.list_tags()]
    assert names == ["Apple", "banana", "zebra"]


def test_list_tags_standalone_zero_count_tag_not_shadowed_by_hashtag(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")

    index.create_tag("python")  # standalone, zero structural entries
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    # A standalone tag with zero linked entries does not block the
    # content-hashtag fallback — it still reflects real usage.
    pairs = dict(index.list_tags())
    assert pairs["python"] == 1


# ---------------------------------------------------------------------------
# get_content_hashtags_for_entries
# ---------------------------------------------------------------------------


def test_get_content_hashtags_for_entries_empty_ids(index: VaultIndex):
    assert index.get_content_hashtags_for_entries([]) == {}


def test_get_content_hashtags_for_entries_single_entry(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python and #dev.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    result = index.get_content_hashtags_for_entries([entry_id])
    assert result == {entry_id: ["dev", "python"]}


def test_get_content_hashtags_for_entries_omits_entries_with_none(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("No hashtags here.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    assert index.get_content_hashtags_for_entries([entry_id]) == {}


def test_get_content_hashtags_for_entries_multiple(index: VaultIndex, tmp_path: Path):
    vault = tmp_path / "pages"
    vault.mkdir()
    file_a = vault / "a.md"
    file_a.write_text("#alpha\n", encoding="utf-8")
    file_b = vault / "b.md"
    file_b.write_text("#beta\n", encoding="utf-8")

    id_a = index.add_entry(_entry(url="https://a.com", file_path=str(file_a)))
    index.index_backlinks(id_a)
    id_b = index.add_entry(_entry(url="https://b.com", file_path=str(file_b)))
    index.index_backlinks(id_b)

    result = index.get_content_hashtags_for_entries([id_a, id_b])
    assert result == {id_a: ["alpha"], id_b: ["beta"]}


def test_get_content_hashtags_for_entries_structural_tag_not_included(
    index: VaultIndex,
):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python"])

    # Structural tags never populate backlink_refs, so they're absent here.
    assert index.get_content_hashtags_for_entries([entry_id]) == {}


def test_get_content_hashtags_for_entries_resolves_structural_casing(
    index: VaultIndex, tmp_path: Path
):
    # A different entry curates the tag structurally as "Python" (capital).
    structural_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(structural_id, ["Python"])

    # This entry only ever mentions it as a lowercase content hashtag.
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    content_id = index.add_entry(_entry(url="https://b.com", file_path=str(src_file)))
    index.index_backlinks(content_id)

    # Display casing matches the structural tag's, not the raw lowercase
    # form stored in backlink_refs — this is what keeps the reading-view
    # Sidebar's tag list case-consistent with the rest of the UI.
    result = index.get_content_hashtags_for_entries([content_id])
    assert result == {content_id: ["Python"]}


# ---------------------------------------------------------------------------
# get_metrics — weekly window starts on Sunday
# ---------------------------------------------------------------------------


def _read_at(days_ago: int = 0) -> str:
    from datetime import timedelta

    return (datetime.now(tz=UTC) - timedelta(days=days_ago)).isoformat()


def _days_since_sunday() -> int:
    """Return how many days ago the most recent Sunday was (0 = today is Sunday)."""
    return (datetime.now(tz=UTC).weekday() + 1) % 7


def test_get_metrics_read_today_counts_in_week(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_status(entry_id, "read")
    m = index.get_metrics()
    assert m["reads_week"] == 1


def test_get_metrics_read_since_sunday_counts_in_week(index: VaultIndex):
    days = _days_since_sunday()
    if days == 0:
        pytest.skip("today is Sunday — boundary already covered by other test")
    entry_id = index.add_entry(_entry())
    index._conn.execute(
        "UPDATE entries SET read_at = ?, status = 'read' WHERE id = ?",
        (_read_at(days_ago=days - 1), entry_id),
    )
    index._conn.commit()
    assert index.get_metrics()["reads_week"] == 1


def test_get_metrics_read_before_sunday_excluded_from_week(index: VaultIndex):
    days = _days_since_sunday()
    entry_id = index.add_entry(_entry())
    index._conn.execute(
        "UPDATE entries SET read_at = ?, status = 'read' WHERE id = ?",
        (_read_at(days_ago=days + 1), entry_id),
    )
    index._conn.commit()
    assert index.get_metrics()["reads_week"] == 0


def test_get_metrics_unread_not_counted(index: VaultIndex):
    index.add_entry(_entry())
    assert index.get_metrics()["reads_week"] == 0
    assert index.get_metrics()["reads_month"] == 0
    assert index.get_metrics()["reads_year"] == 0


# ---------------------------------------------------------------------------
# reconcile_stale_entries — catches files edited outside the app
# ---------------------------------------------------------------------------


def _bump_mtime(path: Path, seconds: float = 5.0) -> None:
    """Advance a file's mtime, simulating an external edit at a later time."""
    import os

    stat = path.stat()
    os.utime(path, (stat.st_atime + seconds, stat.st_mtime + seconds))


def test_index_backlinks_stamps_indexed_mtime(index: VaultIndex, tmp_path: Path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("No links.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))

    index.index_backlinks(entry_id)

    row = index._conn.execute(
        "SELECT indexed_mtime FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()
    assert row["indexed_mtime"] == src_file.stat().st_mtime


def test_reconcile_stale_entries_reindexes_externally_edited_file(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    # Edited outside the app: hashtag removed, mtime bumped.
    src_file.write_text("No hashtags anymore.\n", encoding="utf-8")
    _bump_mtime(src_file)

    count = index.reconcile_stale_entries()

    assert count == 1
    assert index.get_body_hashtag_entry_ids("python") == []


def test_reconcile_stale_entries_refreshes_fts_content(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Original body.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)
    index.update_fts_content(entry_id, "Test Entry", "Original body.\n")

    src_file.write_text("Rewritten body mentions xylophone.\n", encoding="utf-8")
    _bump_mtime(src_file)

    index.reconcile_stale_entries()

    rows = index._conn.execute(
        "SELECT rowid FROM entries_fts WHERE entries_fts MATCH 'xylophone'"
    ).fetchall()
    assert [r[0] for r in rows] == [entry_id]


def test_reconcile_stale_entries_skips_unmodified_file(
    index: VaultIndex, tmp_path: Path
):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)
    before = index._conn.execute(
        "SELECT indexed_mtime FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()["indexed_mtime"]

    count = index.reconcile_stale_entries()

    assert count == 0
    after = index._conn.execute(
        "SELECT indexed_mtime FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()["indexed_mtime"]
    assert after == before


def test_reconcile_stale_entries_treats_null_mtime_as_stale(
    index: VaultIndex, tmp_path: Path
):
    """Legacy rows predating the indexed_mtime column always get one pass."""
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    # Never indexed — indexed_mtime is NULL, unlike after a normal add_entry
    # + index_backlinks flow.

    count = index.reconcile_stale_entries()

    assert count == 1
    assert index.get_body_hashtag_entry_ids("python") == [entry_id]


def test_reconcile_stale_entries_force_reindexes_despite_clean_mtime(
    index: VaultIndex, tmp_path: Path
):
    """force=True reindexes every entry even when mtime says nothing changed —
    but the returned count still reflects mtime drift, not entries touched."""
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    entry_id = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(entry_id)

    # A ghost backlink_refs row the cache shouldn't have, with indexed_mtime
    # left matching the file's current (unchanged) mtime — clean by mtime.
    index._conn.execute(
        "INSERT INTO backlink_refs (source_id, target_text, is_hashtag,"
        " pre, highlight, post) VALUES (?, 'ghost', 1, '', '#ghost', '')",
        (entry_id,),
    )
    index._conn.commit()

    count = index.reconcile_stale_entries(force=True)

    assert (
        index.get_body_hashtag_entry_ids("ghost") == []
    )  # reindexed despite clean mtime
    assert count == 0  # but not counted as stale — mtime never moved


def test_reconcile_stale_entries_skips_missing_file(index: VaultIndex, tmp_path: Path):
    entry_id = index.add_entry(
        _entry(file_path=str(tmp_path / "pages" / "does-not-exist.md"))
    )

    # Must not raise even though the file was never written.
    count = index.reconcile_stale_entries()

    assert count == 0
    assert entry_id is not None


def test_vaultindex_startup_reconciles_stale_entries(tmp_path: Path):
    """The sweep also runs automatically on every VaultIndex construction."""
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")

    db_path = tmp_path / "vault.db"
    first = VaultIndex(db_path)
    entry_id = first.add_entry(_entry(file_path=str(src_file)))
    first.index_backlinks(entry_id)

    src_file.write_text("No hashtags anymore.\n", encoding="utf-8")
    _bump_mtime(src_file)
    first.close()

    second = VaultIndex(db_path)
    try:
        assert second.get_body_hashtag_entry_ids("python") == []
    finally:
        second.close()
