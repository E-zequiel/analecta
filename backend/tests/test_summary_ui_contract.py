"""T8 backend contract: summary in EntryOut/DB, source_type filters.

UI needs (D11/S14): Library lists only captures; the Síntesis section needs
its own query; listings display the bot summary — so the summary (+basis)
also lands in the DB at PATCH time while the frontmatter stays the agent
record (D9).
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from starlette.testclient import TestClient

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.config import AppConfig
from analecta.markdown.frontmatter import body_sha16
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _make_app(tmp_path: Path) -> tuple[FastAPI, VaultIndex, VaultManager]:
    config = AppConfig(vault_path=tmp_path / "vault")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        index = VaultIndex(config.vault_path / "analecta.db")
        app.state.config = config
        app.state.index = index
        app.state.event_bus = EventBus()
        yield
        index.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(entries_router, prefix="/api/v1")
    index = VaultIndex(config.vault_path / "analecta.db")
    vault = VaultManager(config.vault_path)
    vault.ensure_dirs()
    return app, index, vault


@pytest.fixture
def client(
    tmp_path: Path,
) -> Generator[tuple[TestClient, VaultIndex, VaultManager]]:
    app, index, vault = _make_app(tmp_path)
    with TestClient(app) as c:
        yield c, index, vault


def _add(
    index: VaultIndex,
    vault: VaultManager,
    title: str,
    *,
    source_type: str = "article",
) -> EntryRecord:
    dt = datetime.now(tz=UTC)
    path = vault.page_path(title, dt)
    path.write_text(
        f"---\ntitle: '{title}'\n---\n\nCuerpo de {title}.\n", encoding="utf-8"
    )
    entry = EntryRecord(
        title=title,
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        file_path=str(path),
        source_type=source_type,
        created_at=_now(),
        updated_at=_now(),
    )
    entry.id = index.add_entry(entry)
    return entry


def test_migration_adds_summary_columns(index: VaultIndex) -> None:
    cols = {row[1] for row in index._conn.execute("PRAGMA table_info(entries)")}
    assert "summary" in cols
    assert "summary_basis" in cols


def test_entry_out_carries_summary_from_patch(
    client: tuple[TestClient, VaultIndex, VaultManager],
) -> None:
    c, index, vault = client
    entry = _add(index, vault, "With Summary")
    assert entry.id is not None

    r = c.patch(f"/api/v1/entries/{entry.id}", json={"summary": "Resumen visible."})
    assert r.status_code == 200
    data = r.json()
    assert data["summary"] == "Resumen visible."
    assert data["summary_basis"] is not None
    assert len(data["summary_basis"]) == 16

    # Frontmatter is still the agent record (D9).
    text = Path(entry.file_path).read_text(encoding="utf-8")
    fm = yaml.safe_load(text.split("---\n", 2)[1])
    assert fm["summary"] == "Resumen visible."
    assert fm["summary_basis"] == data["summary_basis"]


def test_patch_summary_updates_db_and_file_together(
    client: tuple[TestClient, VaultIndex, VaultManager],
) -> None:
    c, index, vault = client
    entry = _add(index, vault, "Sync Check")
    assert entry.id is not None

    c.patch(f"/api/v1/entries/{entry.id}", json={"summary": "S1."})
    record = index.get_entry(entry.id)
    assert record is not None
    assert record.summary == "S1."

    text = Path(entry.file_path).read_text(encoding="utf-8")
    fm = yaml.safe_load(text.split("---\n", 2)[1])
    assert fm["summary_basis"] == record.summary_basis
    assert fm["summary_basis"] == body_sha16(text)


def test_list_entries_filters_by_source_type(
    client: tuple[TestClient, VaultIndex, VaultManager],
) -> None:
    c, index, vault = client
    _add(index, vault, "Capture One")
    _add(index, vault, "Tube Two", source_type="youtube")

    r = c.get("/api/v1/entries?source_type=article")
    assert r.status_code == 200
    assert {e["title"] for e in r.json()} == {"Capture One"}

    r = c.get("/api/v1/entries?exclude_source_type=synthesis")
    assert r.status_code == 200
    assert {e["source_type"] for e in r.json()} <= {
        "article",
        "youtube",
        "substack",
        "x",
    }


def test_list_entries_excludes_synthesis_from_library_view(
    client: tuple[TestClient, VaultIndex, VaultManager],
) -> None:
    c, index, vault = client
    _add(index, vault, "Plain Capture")
    _add(index, vault, "Synth Thing", source_type="synthesis")

    r = c.get("/api/v1/entries?exclude_source_type=synthesis")
    assert r.status_code == 200
    assert {e["title"] for e in r.json()} == {"Plain Capture"}
