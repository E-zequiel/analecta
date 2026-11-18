"""T7: bot-side ``summary`` (+ ``summary_basis``) and CLI synthesis.

Owner decisions D2/D9:
- Short bot summary lives in the entry's frontmatter (``summary``, ≤ ~500
  chars) with ``summary_basis: <sha256[:16]>`` — the UTF-8 bytes of the
  post-frontmatter body exactly as on disk, no normalization. Any consumer
  compares current hash vs recorded to judge freshness, regardless of who
  mutated the file.
- Deep synthesis is a first-class entry (T3) reachable from the CLI.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from starlette.testclient import TestClient

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.config import AppConfig
from analecta.markdown.frontmatter import body_sha16, set_summary_fields
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager


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


# ---------------------------------------------------------------------------
# frontmatter helpers
# ---------------------------------------------------------------------------


def test_body_sha16_is_deterministic_and_sensitive() -> None:
    text = "---\ntitle: T\n---\n\nBody.\n"
    assert body_sha16(text) == body_sha16(text)
    other = "---\ntitle: T\n---\n\nBody changed.\n"
    assert body_sha16(text) != body_sha16(other)
    assert len(body_sha16(text)) == 16


def test_set_summary_fields_roundtrip() -> None:
    text = "---\ntitle: T\nread_state: unread\n---\n\nBody.\n"
    out = set_summary_fields(text, "Resumen del cuerpo.", "ab" * 8)
    inner = out.split("---\n", 2)[1]
    data = yaml.safe_load(inner)
    assert data["summary"] == "Resumen del cuerpo."
    assert data["summary_basis"] == "ab" * 8
    assert data["title"] == "T"
    assert data["read_state"] == "unread"
    assert "\nBody.\n" in out


def test_set_summary_fields_overwrites_previous() -> None:
    text = "---\ntitle: T\nsummary: old\nsummary_basis: ff\n---\n\nBody.\n"
    out = set_summary_fields(text, "new summary", "ee" * 8)
    data = yaml.safe_load(out.split("---\n", 2)[1])
    assert data["summary"] == "new summary"
    assert data["summary_basis"] == "ee" * 8
    assert "old" not in out


def test_summary_basis_survives_linked_update() -> None:
    from analecta.markdown.frontmatter import update_linked

    text = "---\ntitle: T\n---\n\nBody.\n"
    basis = body_sha16(text)
    text = update_linked(text, add="Other")
    text = set_summary_fields(text, "S", basis)
    text = update_linked(text, add="Third")
    data = yaml.safe_load(text.split("---\n", 2)[1])
    assert data["summary_basis"] == basis


# ---------------------------------------------------------------------------
# API: PATCH summary + POST synthesis
# ---------------------------------------------------------------------------


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
def client(tmp_path: Path) -> Generator[tuple[TestClient, VaultIndex, VaultManager]]:
    app, index, vault = _make_app(tmp_path)
    with TestClient(app) as c:
        yield c, index, vault


def _page_for(vault: VaultManager, title: str, body: str) -> EntryRecord:
    from datetime import UTC, datetime

    dt = datetime.now(tz=UTC)
    path = vault.page_path(title, dt)
    path.write_text(f"---\ntitle: '{title}'\n---\n\n{body}\n", encoding="utf-8")
    entry = EntryRecord(
        title=title,
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        file_path=str(path),
        source_type="article",
        created_at=_now(),
        updated_at=_now(),
    )
    return entry


def test_api_patch_summary_writes_hash(
    client: tuple[TestClient, VaultIndex, VaultManager],
) -> None:
    c, index, vault = client
    entry = _page_for(vault, "Paged Entry", "Cuerpo original.")
    entry.id = index.add_entry(entry)

    r = c.patch(f"/api/v1/entries/{entry.id}", json={"summary": "Resumen corto."})
    assert r.status_code == 200

    text = Path(entry.file_path).read_text(encoding="utf-8")
    data = yaml.safe_load(text.split("---\n", 2)[1])
    assert data["summary"] == "Resumen corto."
    assert data["summary_basis"] == body_sha16(text)
    assert len(data["summary_basis"]) == 16


def test_api_patch_summary_rejects_over_bound(
    client: tuple[TestClient, VaultIndex, VaultManager],
) -> None:
    c, index, vault = client
    entry = _page_for(vault, "Bound Entry", "Cuerpo.")
    entry.id = index.add_entry(entry)

    r = c.patch(f"/api/v1/entries/{entry.id}", json={"summary": "x" * 501})
    assert r.status_code == 400
    assert "500" in r.json()["detail"]


def test_api_post_synthesis_creates_first_class_entry(
    client: tuple[TestClient, VaultIndex, VaultManager],
) -> None:
    c, index, vault = client
    capture = _page_for(vault, "Source Entry", "Contenido.")
    capture.id = index.add_entry(capture)

    r = c.post(
        "/api/v1/synthesis",
        json={
            "title": "Síntesis vía API",
            "body": "Véase [[Source Entry]].",
            "source_ids": [capture.id],
            "generated_by": "bot/gpt",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["source_type"] == "synthesis"

    record = index.get_entry(data["id"])
    assert record is not None
    assert record.url.startswith("analecta://synthesis/")


def test_api_post_synthesis_unknown_source_404(
    client: tuple[TestClient, VaultIndex, VaultManager],
) -> None:
    c, _index, _vault = client
    r = c.post(
        "/api/v1/synthesis",
        json={
            "title": "Huérfana",
            "body": "cuerpo",
            "source_ids": [99999],
            "generated_by": "bot/gpt",
        },
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# CLI commands (standalone, injected vault)
# ---------------------------------------------------------------------------


def test_cli_summary_writes_frontmatter(tmp_path: Path) -> None:
    from analecta.cli import run

    vault = VaultManager(tmp_path / "vault")
    vault.ensure_dirs()
    index = VaultIndex(tmp_path / "vault" / "analecta.db")
    try:
        entry = _page_for(vault, "CLI Summary Target", "Cuerpo a resumir.")
        entry.id = index.add_entry(entry)
        assert entry.id is not None
    finally:
        index.close()

    code = run(
        ["summary", str(entry.id), "--text", "Resumen CLI.", "--by", "bot/x"],
        vault_path=tmp_path / "vault",
    )
    assert code == 0
    text = Path(entry.file_path).read_text(encoding="utf-8")
    data = yaml.safe_load(text.split("---\n", 2)[1])
    assert data["summary"] == "Resumen CLI."
    assert data["summary_basis"] == body_sha16(text)


def test_cli_summary_rejects_over_bound(tmp_path: Path) -> None:
    from analecta.cli import run

    code = run(
        ["summary", "1", "--text", "x" * 501, "--by", "bot/x"],
        vault_path=tmp_path / "vault",
    )
    assert code == 1


def test_cli_synthesize_creates_entry(tmp_path: Path) -> None:
    from analecta.cli import run

    vault = VaultManager(tmp_path / "vault")
    vault.ensure_dirs()
    index = VaultIndex(tmp_path / "vault" / "analecta.db")
    try:
        entry = _page_for(vault, "CLI Source", "Contenido fuente.")
        entry.id = index.add_entry(entry)
        assert entry.id is not None
        eid = entry.id
    finally:
        index.close()

    code = run(
        [
            "synthesize",
            "--title",
            "Síntesis CLI",
            "--body",
            "Véase [[CLI Source]].",
            "--source",
            str(eid),
            "--by",
            "bot/gpt",
            "--json",
        ],
        vault_path=tmp_path / "vault",
    )
    assert code == 0
    index2 = VaultIndex(tmp_path / "vault" / "analecta.db")
    try:
        synths = [r for r in index2.list_entries() if r.source_type == "synthesis"]
        assert len(synths) == 1
        assert synths[0].url.startswith("analecta://synthesis/")
    finally:
        index2.close()
