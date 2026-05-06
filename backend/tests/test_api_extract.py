import asyncio
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from pytest_mock import MockerFixture
from starlette.testclient import TestClient

from analecta.api.routes.extract import router as extract_router
from analecta.api.routes.entries import router as entries_router
from analecta.config import AppConfig
from analecta.extraction.assets import AssetDownloader
from analecta.extraction.core import ExtractionError, ExtractedContent
from analecta.storage.index import EntryRecord, VaultIndex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path) -> FastAPI:
    config = AppConfig(vault_path=tmp_path / "vault")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        index = VaultIndex(config.vault_path / "analecta.db")
        app.state.config = config
        app.state.index = index
        app.state.event_bus = asyncio.Queue[dict[str, object]]()
        yield
        index.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(extract_router, prefix="/api/v1")
    app.include_router(entries_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with TestClient(_make_app(tmp_path)) as c:
        yield c


def _fake_content(url: str = "https://example.com/article") -> ExtractedContent:
    return ExtractedContent(
        title="Test Article",
        html="<p>Hello world</p>",
        url=url,
        source_type="article",
    )


# ---------------------------------------------------------------------------
# POST /extract — happy path
# ---------------------------------------------------------------------------


def test_extract_creates_entry(
    tmp_path: Path, client: TestClient, mocker: MockerFixture
) -> None:
    """POST /extract returns EntryOut and writes vault files."""
    mocker.patch("analecta.api.routes.extract.extract", return_value=_fake_content())
    mocker.patch.object(AssetDownloader, "process", return_value="<p>Hello world</p>")

    r = client.post("/api/v1/extract", json={"url": "https://example.com/article"})

    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Test Article"
    assert data["url"] == "https://example.com/article"
    assert data["source_type"] == "article"
    assert data["status"] == "unread"

    # Markdown file must exist in vault/pages/
    pages = list((tmp_path / "vault" / "pages").glob("*.md"))
    assert len(pages) == 1

    # Entry must be in the index
    r2 = client.get("/api/v1/entries")
    assert r2.status_code == 200
    assert len(r2.json()) == 1


def test_extract_publishes_sse_event(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Successful extraction puts entry_added on the event bus."""
    config = AppConfig(vault_path=tmp_path / "vault")
    bus: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        index = VaultIndex(config.vault_path / "analecta.db")
        app.state.config = config
        app.state.index = index
        app.state.event_bus = bus
        yield
        index.close()

    from fastapi import FastAPI as _FastAPI
    app = _FastAPI(lifespan=lifespan)
    app.include_router(extract_router, prefix="/api/v1")

    mocker.patch("analecta.api.routes.extract.extract", return_value=_fake_content())
    mocker.patch.object(AssetDownloader, "process", return_value="<p>Hello world</p>")

    with TestClient(app) as c:
        c.post("/api/v1/extract", json={"url": "https://example.com/article"})

    assert not bus.empty()
    event = bus.get_nowait()
    assert event["type"] == "entry_added"
    assert isinstance(event["id"], int)


# ---------------------------------------------------------------------------
# POST /extract — error paths
# ---------------------------------------------------------------------------


def test_extract_422_on_extraction_error(
    client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "analecta.api.routes.extract.extract",
        side_effect=ExtractionError("fetch failed"),
    )
    r = client.post("/api/v1/extract", json={"url": "https://example.com/bad"})
    assert r.status_code == 422
    assert "fetch failed" in r.json()["detail"]


def test_extract_422_on_not_implemented(
    client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "analecta.api.routes.extract.extract",
        side_effect=NotImplementedError("X/Twitter not supported"),
    )
    r = client.post("/api/v1/extract", json={"url": "https://x.com/post/1"})
    assert r.status_code == 422


def test_extract_409_on_duplicate_url(
    tmp_path: Path, client: TestClient, mocker: MockerFixture
) -> None:
    """Second POST with the same URL returns 409."""
    mocker.patch("analecta.api.routes.extract.extract", return_value=_fake_content())
    mocker.patch.object(AssetDownloader, "process", return_value="<p>Hello world</p>")

    url = "https://example.com/article"
    r1 = client.post("/api/v1/extract", json={"url": url})
    assert r1.status_code == 200

    r2 = client.post("/api/v1/extract", json={"url": url})
    assert r2.status_code == 409


def test_extract_422_missing_url(client: TestClient) -> None:
    r = client.post("/api/v1/extract", json={})
    assert r.status_code == 422
