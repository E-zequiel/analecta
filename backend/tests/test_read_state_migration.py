"""T1: entry read-state key renamed from ``status`` to ``read_state``.

Covers the migration (SQLite column rename), the storage API, the API routes,
the frontmatter field, and the one-time vault frontmatter rewrite. Read
semantics are unchanged: same values (``unread``/``read``), same ``read_at``
behavior, UI labels untouched.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.config import AppConfig
from analecta.markdown.frontmatter import build_frontmatter
from analecta.storage.index import EntryRecord, VaultIndex


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()


def _entry(**kwargs: Any) -> EntryRecord:
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


def _make_app(tmp_path: Path, index_out: dict[str, VaultIndex]) -> FastAPI:
    config = AppConfig(vault_path=tmp_path / "vault")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        index = VaultIndex(config.vault_path / "analecta.db")
        app.state.config = config
        app.state.index = index
        app.state.event_bus = EventBus()
        index_out["index"] = index
        yield
        index.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(entries_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(
    tmp_path: Path,
) -> Generator[tuple[TestClient, VaultIndex]]:
    index_out: dict[str, VaultIndex] = {}
    app = _make_app(tmp_path, index_out)
    with TestClient(app) as c:
        yield c, index_out["index"]


def _seed(index: VaultIndex, *, n: int = 1, read_state: str = "unread") -> int:
    return index.add_entry(
        _entry(
            title=f"Article {n}",
            url=f"https://example.com/{n}",
            file_path=f"/vault/pages/article-{n}.md",
            read_state=read_state,
        )
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_renames_status_column(index: VaultIndex) -> None:
    cols = {row[1] for row in index._conn.execute("PRAGMA table_info(entries)")}
    assert "read_state" in cols
    assert "status" not in cols


def test_entry_record_uses_read_state(index: VaultIndex) -> None:
    eid = _seed(index, read_state="unread")
    row = index._conn.execute(
        "SELECT read_state FROM entries WHERE id = ?", (eid,)
    ).fetchone()
    assert row["read_state"] == "unread"


def test_update_read_state_sets_read_at(index: VaultIndex) -> None:
    eid = _seed(index)
    index.update_read_state(eid, "read")
    row = index._conn.execute(
        "SELECT read_state, read_at FROM entries WHERE id = ?", (eid,)
    ).fetchone()
    assert row["read_state"] == "read"
    assert row["read_at"] is not None


def test_list_entries_filters_by_read_state(index: VaultIndex) -> None:
    _seed(index, n=1, read_state="unread")
    _seed(index, n=2, read_state="read")
    read_rows = index.list_entries(read_state="read")
    unread_rows = index.list_entries(read_state="unread")
    assert len(read_rows) == 1
    assert len(unread_rows) == 1


def test_entry_record_defaults_read_state_unread(index: VaultIndex) -> None:
    eid = index.add_entry(_entry())
    row = index._conn.execute(
        "SELECT read_state FROM entries WHERE id = ?", (eid,)
    ).fetchone()
    assert row["read_state"] == "unread"


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def test_build_frontmatter_writes_read_state() -> None:
    from analecta.extraction.core import ExtractedContent

    content = ExtractedContent(
        title="T",
        html="<p>x</p>",
        url="https://example.com",
        source_type="article",
        metadata={},
    )
    fm = build_frontmatter(content, _now())
    assert "read_state: unread" in fm
    assert "\nstatus:" not in fm


def test_frontmatter_key_rename_rewrites_vault_files(tmp_path: Path) -> None:
    """The one-time vault rewrite: `status:` -> `read_state:` in page files."""
    from analecta.maintenance import rename_frontmatter_status_keys

    vault = tmp_path / "vault"
    pages = vault / "pages"
    pages.mkdir(parents=True)
    page = pages / "2026-04-30-legacy.md"
    page.write_text(
        "---\n"
        "title: Legacy\n"
        "url: https://example.com/legacy\n"
        "source_type: article\n"
        "created_at: '2026-04-30T00:00:00+00:00'\n"
        "tags: []\n"
        "status: read\n"
        "---\n"
        "\nBody with [[Wikilink]] and #tag.\n",
        encoding="utf-8",
    )
    # A file without frontmatter must be left untouched.
    plain = pages / "2026-04-30-plain.md"
    plain.write_text("No frontmatter here.\n", encoding="utf-8")

    changed = rename_frontmatter_status_keys(vault)

    assert changed == 1
    text = page.read_text(encoding="utf-8")
    assert "read_state: read" in text
    assert "\nstatus:" not in text
    assert "Body with [[Wikilink]] and #tag." in text
    assert plain.read_text(encoding="utf-8") == "No frontmatter here.\n"

    # Idempotent: second run changes nothing.
    assert rename_frontmatter_status_keys(vault) == 0


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


def test_api_patch_entry_read_state(
    client: tuple[TestClient, VaultIndex],
) -> None:
    c, index = client
    eid = _seed(index)

    r = c.patch(f"/api/v1/entries/{eid}", json={"read_state": "read"})
    assert r.status_code == 200
    data = r.json()
    assert data["read_state"] == "read"
    assert "status" not in data


def test_api_list_entries_filters_by_read_state(
    client: tuple[TestClient, VaultIndex],
) -> None:
    c, index = client
    _seed(index, n=1, read_state="unread")
    _seed(index, n=2, read_state="read")

    r = c.get("/api/v1/entries?read_state=read")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["read_state"] == "read"
    assert "status" not in data[0]
