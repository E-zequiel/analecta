from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI

from analecta.api.events import EventBus
from analecta.api.routes.config import router as config_router
from analecta.api.routes.entries import router as entries_router
from analecta.api.routes.extract import router as extract_router
from analecta.api.routes.pkm import router as pkm_router
from analecta.api.routes.search import router as search_router
from analecta.api.routes.system import router as system_router
from analecta.api.routes.tags import router as tags_router
from analecta.config import AppConfig
from analecta.storage.index import VaultIndex
from analecta.storage.vault import VaultManager

# ---------------------------------------------------------------------------
# Shared primitive fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Temp directory used as the Analecta vault root."""
    return tmp_path / "vault"


@pytest.fixture
def app_config(tmp_vault: Path) -> AppConfig:
    """AppConfig pointed at tmp_vault."""
    return AppConfig(vault_path=tmp_vault)


@pytest.fixture
def index(tmp_vault: Path) -> Generator[VaultIndex]:
    """Open VaultIndex backed by a temp SQLite DB; closed after the test."""
    idx = VaultIndex(tmp_vault / "analecta.db")
    yield idx
    idx.close()


@pytest.fixture
def vault(tmp_vault: Path) -> VaultManager:
    """VaultManager pointed at tmp_vault."""
    return VaultManager(tmp_vault)


# ---------------------------------------------------------------------------
# Full-app async HTTP client (all routers)
# ---------------------------------------------------------------------------


def _build_full_app(cfg: AppConfig) -> FastAPI:
    """FastAPI app with every router registered; used by the shared client fixture."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        idx = VaultIndex(cfg.vault_path / "analecta.db")
        app.state.config = cfg
        app.state.index = idx
        app.state.event_bus = EventBus()
        app.state.port = 0
        yield
        idx.close()

    app = FastAPI(lifespan=lifespan)
    for router in (
        config_router,
        entries_router,
        extract_router,
        pkm_router,
        search_router,
        system_router,
        tags_router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app_config: AppConfig) -> AsyncGenerator[httpx2.AsyncClient]:
    """Async httpx client backed by a full in-process FastAPI app (all routes).

    Uses httpx2.ASGITransport — no real TCP sockets are opened.
    Do NOT use starlette.testclient.TestClient; it cannot handle streaming SSE
    responses.
    """
    app = _build_full_app(app_config)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
