import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager, _slugify

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _entry(**kwargs) -> EntryRecord:
    defaults = dict(
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
    dt = datetime(2026, 4, 30, tzinfo=timezone.utc)
    assert vault.page_path("My Article", dt).name == "2026-04-30-my-article.md"


def test_vault_page_path_fallback_slug(vault: VaultManager):
    dt = datetime(2026, 4, 30, tzinfo=timezone.utc)
    assert vault.page_path("!!!", dt).name == "2026-04-30-entry.md"


def test_vault_write_page_creates_file(vault: VaultManager):
    dt = datetime(2026, 4, 30, tzinfo=timezone.utc)
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


# ---------------------------------------------------------------------------
# VaultIndex — CRUD
# ---------------------------------------------------------------------------


def test_add_entry_returns_int(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    assert isinstance(entry_id, int) and entry_id >= 1


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
    assert index.get_entry(entry_id).status == "read"


def test_soft_delete_sets_deleted_status(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.soft_delete(entry_id)
    assert index.get_entry(entry_id).status == "deleted"


def test_update_tags_sets_json(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    assert json.loads(index.get_entry(entry_id).tags_json) == ["python", "sqlite"]


def test_update_tags_syncs_tags_table(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    row = index._conn.execute("SELECT count FROM tags WHERE name = 'python'").fetchone()
    assert row[0] == 1


def test_update_tags_replaces_previous(index: VaultIndex):
    entry_id = index.add_entry(_entry())
    index.update_tags(entry_id, ["python", "sqlite"])
    index.update_tags(entry_id, ["python"])
    row = index._conn.execute("SELECT count FROM tags WHERE name = 'sqlite'").fetchone()
    assert row[0] == 0


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


def test_search_invalid_fts_syntax_raises(index: VaultIndex):
    with pytest.raises(sqlite3.OperationalError):
        index.search("AND")
