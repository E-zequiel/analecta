import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from pytest_mock import MockerFixture
from starlette.responses import StreamingResponse
from starlette.testclient import TestClient

from analecta.api.events import EventBus
from analecta.api.routes.config import router as config_router
from analecta.api.routes.pkm import router as pkm_router
from analecta.api.routes.system import router as system_router
from analecta.config import AppConfig
from analecta.extraction.assets import AssetDownloader, _placeholder_filename
from analecta.storage.index import EntryRecord, VaultIndex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_entry(index: VaultIndex, url: str = "https://example.com/1") -> int:
    return index.add_entry(
        EntryRecord(
            title="Article 1",
            url=url,
            file_path="/vault/pages/article-1.md",
            source_type="article",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            status="unread",
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
        app.state.port = 0
        yield
        index.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(config_router, prefix="/api/v1")
    app.include_router(pkm_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient]:
    with TestClient(_make_app(tmp_path)) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


def test_get_config_200(client: TestClient) -> None:
    r = client.get("/api/v1/config")
    assert r.status_code == 200
    assert "vault_path" in r.json()


# ---------------------------------------------------------------------------
# PUT /config
# ---------------------------------------------------------------------------


def test_put_config_200(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch("analecta.api.routes.config.save_config")
    with TestClient(_make_app(tmp_path)) as c:
        r = c.put("/api/v1/config", json={"theme": "light"})
    assert r.status_code == 200
    assert r.json()["theme"] == "light"


def test_put_config_close_to_tray(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch("analecta.api.routes.config.save_config")
    with TestClient(_make_app(tmp_path)) as c:
        r = c.put("/api/v1/config", json={"close_to_tray": False})
    assert r.status_code == 200
    assert r.json()["close_to_tray"] is False


def test_put_config_invalid_font_422(client: TestClient) -> None:
    r = client.put("/api/v1/config", json={"font_variant": "not-a-variant"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /system/health
# ---------------------------------------------------------------------------


def test_health_200(client: TestClient) -> None:
    r = client.get("/api/v1/system/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "port" in data


def test_health_port_is_int(client: TestClient) -> None:
    r = client.get("/api/v1/system/health")
    assert isinstance(r.json()["port"], int)


# ---------------------------------------------------------------------------
# GET /system/events (SSE)
# ---------------------------------------------------------------------------


def test_events_200(tmp_path: Path, mocker: MockerFixture) -> None:
    # starlette TestClient's portal.call() blocks until the ASGI app finishes,
    # which never happens for an infinite SSE stream.  We replace
    # EventSourceResponse with a finite StreamingResponse so the handler
    # returns normally and we can verify the 200 + content-type.
    def _fake_event_source_response(content: object, **kw: object) -> StreamingResponse:
        return StreamingResponse(
            iter([b"data: {}\n\n"]), media_type="text/event-stream"
        )

    mocker.patch(
        "analecta.api.routes.system.EventSourceResponse",
        side_effect=_fake_event_source_response,
    )
    with TestClient(_make_app(tmp_path)) as c:
        r = c.get("/api/v1/system/events")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# POST /system/rescan
# ---------------------------------------------------------------------------


def test_rescan_200_returns_updated_count(client: TestClient) -> None:
    r = client.post("/api/v1/system/rescan")
    assert r.status_code == 200
    assert r.json() == {"updated": 0}


def test_rescan_publishes_sse_event(tmp_path: Path) -> None:
    """A rescan has no single changed entry to report, so the frontend
    can't infer it happened the way it does for an in-app edit — it must
    be told, or an already-open window's Sidebar tag list (and anything
    else derived from backlink_refs) never refreshes."""
    app = _make_app(tmp_path)
    with TestClient(app) as c:
        bus: EventBus = app.state.event_bus
        sink: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        bus._queues.append(sink)

        r = c.post("/api/v1/system/rescan")
        assert r.status_code == 200

    assert not sink.empty()
    assert sink.get_nowait() == {"type": "vault_rescanned"}


def test_rescan_reindexes_entry_edited_after_startup(tmp_path: Path) -> None:
    """The manual endpoint catches edits made while the sidecar is already
    running — the one case the automatic startup sweep can't see."""
    vault = tmp_path / "vault"
    pages = vault / "pages"
    pages.mkdir(parents=True)
    src_file = pages / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")

    app = _make_app(tmp_path)
    with TestClient(app) as c:
        index: VaultIndex = app.state.index
        entry_id = index.add_entry(
            EntryRecord(
                title="Article",
                url="https://example.com/article",
                file_path=str(src_file),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )
        index.index_backlinks(entry_id)

        src_file.write_text("No hashtags anymore.\n", encoding="utf-8")
        stat = src_file.stat()
        os.utime(src_file, (stat.st_atime + 5, stat.st_mtime + 5))

        r = c.post("/api/v1/system/rescan")
        assert r.status_code == 200
        assert r.json() == {"updated": 1}
        assert index.get_body_hashtag_entry_ids("python") == []


# ---------------------------------------------------------------------------
# POST /system/localize-images
# ---------------------------------------------------------------------------


def test_localize_images_200_empty_vault(client: TestClient) -> None:
    r = client.post("/api/v1/system/localize-images")
    assert r.status_code == 200
    assert r.json() == {"updated": 0, "placeholders": 0}


def test_localize_images_rewrites_entry_with_remote_ref(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    filename = "abc123def45678.png"
    mocker.patch.object(AssetDownloader, "_download", return_value=filename)

    vault = tmp_path / "vault"
    pages = vault / "pages"
    pages.mkdir(parents=True)
    src_file = pages / "2026-05-26-wikipedia.md"
    src_file.write_text(
        "# Wikipedia\n\n![logo](//upload.wikimedia.org/logo.png)\n", encoding="utf-8"
    )

    app = _make_app(tmp_path)
    with TestClient(app) as c:
        index: VaultIndex = app.state.index
        index.add_entry(
            EntryRecord(
                title="Wikipedia",
                url="https://es.wikipedia.org/wiki/Foo",
                file_path=str(src_file),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )

        r = c.post("/api/v1/system/localize-images")
        assert r.status_code == 200
        assert r.json() == {"updated": 1, "placeholders": 0}

    content = src_file.read_text(encoding="utf-8")
    assert f"../assets/2026-05-26-wikipedia/{filename}" in content
    assert "//upload.wikimedia.org/logo.png" not in content


def test_localize_images_counts_placeholders(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch.object(
        AssetDownloader, "_download", return_value=_placeholder_filename()
    )

    vault = tmp_path / "vault"
    pages = vault / "pages"
    pages.mkdir(parents=True)
    src_file = pages / "article.md"
    src_file.write_text("![gone](https://example.com/dead.png)\n", encoding="utf-8")

    app = _make_app(tmp_path)
    with TestClient(app) as c:
        index: VaultIndex = app.state.index
        index.add_entry(
            EntryRecord(
                title="Article",
                url="https://example.com/article",
                file_path=str(src_file),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )

        r = c.post("/api/v1/system/localize-images")
        assert r.status_code == 200
        assert r.json() == {"updated": 1, "placeholders": 1}


def test_localize_images_leaves_unaffected_entry_untouched(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    pages = vault / "pages"
    pages.mkdir(parents=True)
    src_file = pages / "article.md"
    original = "# Title\n\n![local](../assets/article/abc123.png)\n"
    src_file.write_text(original, encoding="utf-8")

    app = _make_app(tmp_path)
    with TestClient(app) as c:
        index: VaultIndex = app.state.index
        index.add_entry(
            EntryRecord(
                title="Article",
                url="https://example.com/article",
                file_path=str(src_file),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )

        r = c.post("/api/v1/system/localize-images")
        assert r.status_code == 200
        assert r.json() == {"updated": 0, "placeholders": 0}

    assert src_file.read_text(encoding="utf-8") == original


def test_localize_images_skips_entry_with_missing_file(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    with TestClient(app) as c:
        index: VaultIndex = app.state.index
        index.add_entry(
            EntryRecord(
                title="Gone",
                url="https://example.com/gone",
                file_path=str(tmp_path / "vault" / "pages" / "missing.md"),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )

        r = c.post("/api/v1/system/localize-images")
        assert r.status_code == 200
        assert r.json() == {"updated": 0, "placeholders": 0}


def test_localize_images_publishes_sse_event_when_something_changed(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch.object(AssetDownloader, "_download", return_value="abc123.png")

    vault = tmp_path / "vault"
    pages = vault / "pages"
    pages.mkdir(parents=True)
    src_file = pages / "article.md"
    src_file.write_text("![x](https://example.com/x.png)\n", encoding="utf-8")

    app = _make_app(tmp_path)
    with TestClient(app) as c:
        index: VaultIndex = app.state.index
        index.add_entry(
            EntryRecord(
                title="Article",
                url="https://example.com/article",
                file_path=str(src_file),
                source_type="article",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )

        bus: EventBus = app.state.event_bus
        sink: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        bus._queues.append(sink)

        r = c.post("/api/v1/system/localize-images")
        assert r.status_code == 200

    assert not sink.empty()
    assert sink.get_nowait() == {"type": "vault_rescanned"}


def test_localize_images_no_sse_event_when_nothing_changed(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    with TestClient(app) as c:
        bus: EventBus = app.state.event_bus
        sink: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        bus._queues.append(sink)

        r = c.post("/api/v1/system/localize-images")
        assert r.status_code == 200

    assert sink.empty()


# ---------------------------------------------------------------------------
# GET /pkm/parse-url
# ---------------------------------------------------------------------------


def test_parse_url_valid(client: TestClient) -> None:
    r = client.get("/api/v1/pkm/parse-url", params={"url": "analecta://open?id=42"})
    assert r.status_code == 200
    assert r.json() == {"entry_id": 42}


def test_parse_url_invalid(client: TestClient) -> None:
    r = client.get("/api/v1/pkm/parse-url", params={"url": "https://not-analecta.com"})
    assert r.status_code == 200
    assert r.json() == {"entry_id": None}


def test_parse_url_zero_id_null(client: TestClient) -> None:
    r = client.get("/api/v1/pkm/parse-url", params={"url": "analecta://open?id=0"})
    assert r.status_code == 200
    assert r.json() == {"entry_id": None}
