from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from pytest_mock import MockerFixture
from starlette.responses import StreamingResponse
from starlette.testclient import TestClient

from analecta.api.events import EventBus
from analecta.api.routes.config import router as config_router
from analecta.api.routes.pkm import router as pkm_router
from analecta.api.routes.security import router as security_router
from analecta.api.routes.system import router as system_router
from analecta.config import AppConfig
from analecta.security.virustotal import ScanResult, VirusTotalScanner
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


def _make_app(
    tmp_path: Path,
    *,
    virustotal_enabled: bool = False,
) -> FastAPI:
    config = AppConfig(
        vault_path=tmp_path / "vault",
        virustotal_enabled=virustotal_enabled,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
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
    app.include_router(security_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with TestClient(_make_app(tmp_path)) as c:
        yield c


@pytest.fixture
def vt_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """App with virustotal_enabled=True and one seeded entry."""
    app = _make_app(tmp_path, virustotal_enabled=True)
    cfg = AppConfig(vault_path=tmp_path / "vault", virustotal_enabled=True)
    with VaultIndex(cfg.vault_path / "analecta.db") as idx:
        _seed_entry(idx)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


def test_get_config_200(client: TestClient) -> None:
    r = client.get("/api/v1/config")
    assert r.status_code == 200
    data = r.json()
    assert "vault_path" in data
    assert "virustotal_enabled" in data


# ---------------------------------------------------------------------------
# PUT /config
# ---------------------------------------------------------------------------


def test_put_config_200(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch("analecta.api.routes.config.save_config")
    with TestClient(_make_app(tmp_path)) as c:
        r = c.put("/api/v1/config", json={"virustotal_enabled": True})
    assert r.status_code == 200
    assert r.json()["virustotal_enabled"] is True


def test_put_config_invalid_font_422(client: TestClient) -> None:
    r = client.put("/api/v1/config", json={"font_variant": "not-a-variant"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /security/virustotal/key/exists
# ---------------------------------------------------------------------------


def test_key_exists_false_initially(client: TestClient) -> None:
    r = client.get("/api/v1/security/virustotal/key/exists")
    assert r.status_code == 200
    assert r.json() == {"exists": False}


# ---------------------------------------------------------------------------
# PUT /security/virustotal/key
# ---------------------------------------------------------------------------


def test_set_key_204(client: TestClient) -> None:
    r = client.put("/api/v1/security/virustotal/key", json={"value": "test-api-key"})
    assert r.status_code == 204


def test_key_exists_true_after_set(client: TestClient) -> None:
    client.put("/api/v1/security/virustotal/key", json={"value": "test-api-key"})
    r = client.get("/api/v1/security/virustotal/key/exists")
    assert r.status_code == 200
    assert r.json() == {"exists": True}


# ---------------------------------------------------------------------------
# POST /security/virustotal/scan
# ---------------------------------------------------------------------------


def test_scan_403_when_vt_disabled(client: TestClient) -> None:
    r = client.post("/api/v1/security/virustotal/scan", json={"entry_id": 1})
    assert r.status_code == 403


def test_scan_404_unknown_entry(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path, virustotal_enabled=True)) as c:
        r = c.post("/api/v1/security/virustotal/scan", json={"entry_id": 9999})
    assert r.status_code == 404


def test_scan_200(vt_client: TestClient, mocker: MockerFixture) -> None:
    fake_result = ScanResult(
        url="https://example.com/1",
        analysis_id="test-id",
        malicious=0,
        suspicious=0,
        undetected=80,
        harmless=10,
    )
    mocker.patch.object(
        VirusTotalScanner, "scan", new_callable=AsyncMock, return_value=fake_result
    )

    r = vt_client.post("/api/v1/security/virustotal/scan", json={"entry_id": 1})
    assert r.status_code == 200
    data = r.json()
    assert data["verdict"] == "clean"
    assert data["entry_id"] == 1
    assert data["total"] == 90


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
    mocker.patch(
        "analecta.api.routes.system.EventSourceResponse",
        side_effect=lambda content, **kw: StreamingResponse(
            iter([b"data: {}\n\n"]), media_type="text/event-stream"
        ),
    )
    with TestClient(_make_app(tmp_path)) as c:
        r = c.get("/api/v1/system/events")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")


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
