import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from starlette.testclient import TestClient

from analecta.api.deps import get_config, get_event_bus, get_index, get_vault
from analecta.config import AppConfig
from analecta.storage.index import VaultIndex
from analecta.storage.vault import VaultManager


def _make_app(tmp_path: Path) -> FastAPI:
    config = AppConfig(vault_path=tmp_path / "vault")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.config = config
        app.state.index = VaultIndex(config.vault_path / "analecta.db")
        app.state.event_bus = asyncio.Queue[dict[str, object]]()
        yield
        app.state.index.close()

    test_app = FastAPI(lifespan=lifespan)

    @test_app.get("/test/index-id")
    async def _index_id(index: VaultIndex = Depends(get_index)) -> dict[str, int]:
        return {"id": id(index)}

    @test_app.get("/test/vault-type")
    async def _vault_type(vault: VaultManager = Depends(get_vault)) -> dict[str, str]:
        return {"type": type(vault).__name__}

    @test_app.get("/test/bus-type")
    async def _bus_type(
        bus: "asyncio.Queue[dict[str, object]]" = Depends(get_event_bus),
    ) -> dict[str, str]:
        return {"type": type(bus).__name__}

    @test_app.get("/test/config-vault")
    async def _config_vault(
        cfg: AppConfig = Depends(get_config),
    ) -> dict[str, str]:
        return {"vault_path": str(cfg.vault_path)}

    return test_app


def test_get_index_singleton(tmp_path: Path) -> None:
    """Two requests to a Depends(get_index) route must return the same instance."""
    with TestClient(_make_app(tmp_path)) as client:
        r1 = client.get("/test/index-id")
        r2 = client.get("/test/index-id")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


def test_get_vault_returns_vault_manager(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        r = client.get("/test/vault-type")
    assert r.status_code == 200
    assert r.json()["type"] == "VaultManager"


def test_get_event_bus_singleton(tmp_path: Path) -> None:
    with TestClient(_make_app(tmp_path)) as client:
        r = client.get("/test/bus-type")
    assert r.status_code == 200
    assert r.json()["type"] == "Queue"


def test_get_config_returns_app_config(tmp_path: Path) -> None:
    expected = str(tmp_path / "vault")
    with TestClient(_make_app(tmp_path)) as client:
        r = client.get("/test/config-vault")
    assert r.status_code == 200
    assert r.json()["vault_path"] == expected
