from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from pytest_mock import MockerFixture
from starlette.testclient import TestClient

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.api.routes.search import router as search_router
from analecta.api.routes.tags import router as tags_router
from analecta.config import AppConfig
from analecta.storage.index import EntryRecord, VaultIndex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_entry(index: VaultIndex, *, n: int = 1, status: str = "unread") -> int:
    return index.add_entry(
        EntryRecord(
            title=f"Article {n}",
            url=f"https://example.com/{n}",
            file_path=f"/vault/pages/article-{n}.md",
            source_type="article",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            status=status,
        )
    )


def _make_app(tmp_path: Path) -> FastAPI:
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
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(tags_router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient]:
    """Empty app — no entries."""
    with TestClient(_make_app(tmp_path)) as c:
        yield c


@pytest.fixture
def seeded_client(tmp_path: Path) -> Generator[TestClient]:
    """App pre-seeded with two entries (unread tagged 'python', read)."""
    app = _make_app(tmp_path)
    config = AppConfig(vault_path=tmp_path / "vault")
    # Seed via a separate connection before starting the lifespan
    with VaultIndex(config.vault_path / "analecta.db") as index:
        e1 = _seed_entry(index, n=1, status="unread")
        index.update_tags(e1, ["python"])
        index.update_fts_content(e1, "Article 1", "Python programming guide")
        _seed_entry(index, n=2, status="read")
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /entries
# ---------------------------------------------------------------------------


def test_list_entries_empty(client: TestClient) -> None:
    r = client.get("/api/v1/entries")
    assert r.status_code == 200
    assert r.json() == []


def test_list_entries_returns_all(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/v1/entries")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_entries_filter_status(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/v1/entries?status=read")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["status"] == "read"


def test_list_entries_filter_tag(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/v1/entries?tag=python")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["title"] == "Article 1"


def test_list_entries_search(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/v1/entries?q=Python")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ---------------------------------------------------------------------------
# GET /entries/titles
# ---------------------------------------------------------------------------


def test_list_entry_titles_empty(client: TestClient) -> None:
    r = client.get("/api/v1/entries/titles")
    assert r.status_code == 200
    assert r.json() == []


def test_list_entry_titles_returns_id_and_title_only(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/v1/entries/titles")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert {"id", "title"} == set(data[0].keys())
    assert {row["title"] for row in data} == {"Article 1", "Article 2"}


# ---------------------------------------------------------------------------
# GET /entries/{id}
# ---------------------------------------------------------------------------


def test_get_entry_200(seeded_client: TestClient) -> None:
    entries = seeded_client.get("/api/v1/entries").json()
    entry_id = entries[0]["id"]
    r = seeded_client.get(f"/api/v1/entries/{entry_id}")
    assert r.status_code == 200
    assert r.json()["id"] == entry_id


def test_get_entry_404(client: TestClient) -> None:
    r = client.get("/api/v1/entries/9999")
    assert r.status_code == 404


def test_get_entry_includes_content_tags(client: TestClient, tmp_path: Path) -> None:
    vault = tmp_path / "vault" / "pages"
    vault.mkdir(parents=True)
    src_file = vault / "article.md"
    src_file.write_text("Original content.\n", encoding="utf-8")

    config = AppConfig(vault_path=tmp_path / "vault")
    with VaultIndex(config.vault_path / "analecta.db") as index:
        entry_id = index.add_entry(
            EntryRecord(
                title="Fresh",
                url="https://example.com/fresh",
                file_path=str(src_file),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )

    new_content = "Filed under #python.\n"
    src_file.write_text(new_content, encoding="utf-8")
    r = client.patch(
        f"/api/v1/entries/{entry_id}",
        json={"fts": {"title": "Fresh", "content": new_content}},
    )
    assert r.status_code == 200
    assert r.json()["content_tags"] == ["python"]
    assert r.json()["tags"] == []

    r = client.get(f"/api/v1/entries/{entry_id}")
    assert r.status_code == 200
    assert r.json()["content_tags"] == ["python"]


# ---------------------------------------------------------------------------
# PATCH /entries/{id}
# ---------------------------------------------------------------------------


def test_patch_entry_status(seeded_client: TestClient) -> None:
    entry_id = seeded_client.get("/api/v1/entries").json()[0]["id"]
    r = seeded_client.patch(f"/api/v1/entries/{entry_id}", json={"status": "read"})
    assert r.status_code == 200
    assert r.json()["status"] == "read"


def test_patch_entry_tags(seeded_client: TestClient) -> None:
    entry_id = seeded_client.get("/api/v1/entries").json()[0]["id"]
    r = seeded_client.patch(
        f"/api/v1/entries/{entry_id}", json={"tags": ["rust", "wasm"]}
    )
    assert r.status_code == 200
    assert set(r.json()["tags"]) == {"rust", "wasm"}


def test_patch_entry_flags(seeded_client: TestClient) -> None:
    entry_id = seeded_client.get("/api/v1/entries").json()[0]["id"]
    r = seeded_client.patch(f"/api/v1/entries/{entry_id}", json={"flags": ["bookmark"]})
    assert r.status_code == 200
    assert r.json()["flags"] == ["bookmark"]


def test_list_entries_filter_flag(seeded_client: TestClient) -> None:
    entry_id = seeded_client.get("/api/v1/entries").json()[0]["id"]
    seeded_client.patch(f"/api/v1/entries/{entry_id}", json={"flags": ["gem"]})
    r = seeded_client.get("/api/v1/entries?flag=gem")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == entry_id


def test_patch_entry_404(client: TestClient) -> None:
    r = client.patch("/api/v1/entries/9999", json={"status": "read"})
    assert r.status_code == 404


def test_patch_entry_422_invalid_body(client: TestClient) -> None:
    r = client.patch("/api/v1/entries/1", json={"tags": "not-a-list"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /entries/{id}
# ---------------------------------------------------------------------------


def test_delete_entry_204(seeded_client: TestClient) -> None:
    entry_id = seeded_client.get("/api/v1/entries").json()[0]["id"]
    r = seeded_client.delete(f"/api/v1/entries/{entry_id}")
    assert r.status_code == 204
    r2 = seeded_client.get(f"/api/v1/entries/{entry_id}")
    assert r2.status_code == 404


def test_delete_entry_404(client: TestClient) -> None:
    r = client.delete("/api/v1/entries/9999")
    assert r.status_code == 404


def test_delete_entry_removes_assets_dir(tmp_path: Path) -> None:
    pages_dir = tmp_path / "vault" / "pages"
    pages_dir.mkdir(parents=True)
    md_file = pages_dir / "2024-01-01-my-article.md"
    md_file.write_text("# My Article\n")
    assets_dir = tmp_path / "vault" / "assets" / md_file.stem
    assets_dir.mkdir(parents=True)
    (assets_dir / "image.jpg").write_bytes(b"fake")

    app = _make_app(tmp_path)
    config = AppConfig(vault_path=tmp_path / "vault")
    with VaultIndex(config.vault_path / "analecta.db") as index:
        entry_id = index.add_entry(
            EntryRecord(
                title="My Article",
                url="https://example.com/my-article",
                file_path=str(md_file),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
                status="unread",
            )
        )

    with TestClient(app) as c:
        r = c.delete(f"/api/v1/entries/{entry_id}")

    assert r.status_code == 204
    assert not assets_dir.exists()
    assert not md_file.exists()


# ---------------------------------------------------------------------------
# GET /tags
# ---------------------------------------------------------------------------


def test_list_tags(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/v1/tags")
    assert r.status_code == 200
    assert any(t["name"] == "python" for t in r.json())


def test_list_tags_empty(client: TestClient) -> None:
    r = client.get("/api/v1/tags")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# POST /tags
# ---------------------------------------------------------------------------


def test_create_tag(client: TestClient) -> None:
    r = client.post("/api/v1/tags", json={"name": "python"})
    assert r.status_code == 201
    assert r.json()["name"] == "python"
    assert r.json()["count"] == 0


def test_create_tag_idempotent(client: TestClient) -> None:
    client.post("/api/v1/tags", json={"name": "python"})
    r = client.post("/api/v1/tags", json={"name": "python"})
    assert r.status_code == 201


def test_create_tag_case_insensitive_returns_existing_canonical(
    seeded_client: TestClient,
) -> None:
    # seeded_client already has one entry tagged "python" (count 1).
    r = seeded_client.post("/api/v1/tags", json={"name": "PYTHON"})
    assert r.status_code == 201
    assert r.json() == {"name": "python", "count": 1}


# ---------------------------------------------------------------------------
# PUT /tags/{name}
# ---------------------------------------------------------------------------


def test_rename_tag(seeded_client: TestClient) -> None:
    r = seeded_client.put("/api/v1/tags/python", json={"new_name": "py"})
    assert r.status_code == 200
    assert r.json()["name"] == "py"
    tags = seeded_client.get("/api/v1/tags").json()
    names = [t["name"] for t in tags]
    assert "py" in names
    assert "python" not in names


def test_rename_tag_conflict_409(seeded_client: TestClient) -> None:
    seeded_client.post("/api/v1/tags", json={"name": "rust"})
    r = seeded_client.put("/api/v1/tags/python", json={"new_name": "rust"})
    assert r.status_code == 409


def test_rename_tag_case_insensitive_lookup(seeded_client: TestClient) -> None:
    # seeded_client's tag is stored as "python"; URL param differs in case.
    r = seeded_client.put("/api/v1/tags/PYTHON", json={"new_name": "py"})
    assert r.status_code == 200
    assert r.json() == {"name": "py", "count": 1}


def test_rename_tag_nonexistent_returns_200_zero_count(client: TestClient) -> None:
    r = client.put("/api/v1/tags/nonexistent", json={"new_name": "other"})
    assert r.status_code == 200
    assert r.json() == {"name": "other", "count": 0}


# ---------------------------------------------------------------------------
# DELETE /tags/{name}
# ---------------------------------------------------------------------------


def test_delete_tag(seeded_client: TestClient) -> None:
    r = seeded_client.delete("/api/v1/tags/python")
    assert r.status_code == 204
    tags = seeded_client.get("/api/v1/tags").json()
    assert not any(t["name"] == "python" for t in tags)


def test_delete_tag_nonexistent_204(client: TestClient) -> None:
    r = client.delete("/api/v1/tags/nonexistent")
    assert r.status_code == 204


def test_delete_tag_case_insensitive_lookup(seeded_client: TestClient) -> None:
    # seeded_client's tag is stored as "python"; URL param differs in case.
    r = seeded_client.delete("/api/v1/tags/PYTHON")
    assert r.status_code == 204
    tags = seeded_client.get("/api/v1/tags").json()
    assert not any(t["name"] == "python" for t in tags)


# ---------------------------------------------------------------------------
# GET /tags/{name}/body-count
# ---------------------------------------------------------------------------


def test_tag_body_count_finds_content_hashtag(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    pages = vault / "pages"
    pages.mkdir(parents=True)
    src_file = pages / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")

    config = AppConfig(vault_path=vault)
    with VaultIndex(config.vault_path / "analecta.db") as index:
        entry_id = index.add_entry(
            EntryRecord(
                title="Article",
                url="https://example.com/1",
                file_path=str(src_file),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )
        index.index_backlinks(entry_id)

    with TestClient(_make_app(tmp_path)) as c:
        r = c.get("/api/v1/tags/python/body-count")
        assert r.status_code == 200
        assert r.json() == {"count": 1}


def test_tag_body_count_zero_when_structural_only(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/v1/tags/python/body-count")
    assert r.status_code == 200
    assert r.json() == {"count": 0}


# ---------------------------------------------------------------------------
# GET /search
# ---------------------------------------------------------------------------


def test_search_endpoint(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/v1/search?q=programming")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_search_empty_q_422(client: TestClient) -> None:
    r = client.get("/api/v1/search?q=")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 500 coverage
# ---------------------------------------------------------------------------


def test_entries_500_on_db_error(tmp_path: Path, mocker: MockerFixture) -> None:
    """An unhandled DB exception returns HTTP 500."""
    mocker.patch.object(VaultIndex, "list_entries", side_effect=RuntimeError("db boom"))
    with TestClient(_make_app(tmp_path), raise_server_exceptions=False) as c:
        r = c.get("/api/v1/entries")
    assert r.status_code == 500
