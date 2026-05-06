import asyncio
import logging
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analecta.api.events import EventBus
from analecta.api.routes import (
    config,
    entries,
    extract,
    pkm,
    search,
    security,
    system,
    tags,
)
from analecta.config import load_config, setup_logging
from analecta.storage.index import VaultIndex

log = logging.getLogger(__name__)


def _find_free_port() -> int:
    """Bind to a random OS-assigned port and return its number.

    Returns:
        An available TCP port on the loopback interface.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan — initialises singletons and signals sidecar readiness."""
    config = load_config()
    index = VaultIndex(config.vault_path / "analecta.db")
    app.state.config = config
    app.state.index = index
    app.state.event_bus = EventBus()
    log.info("sidecar ready")
    print("SIDECAR_READY", flush=True)
    yield
    index.close()
    log.info("sidecar shut down")


app = FastAPI(title="Analecta", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://tauri.localhost"],
    allow_origin_regex=r"http://localhost(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(config.router, prefix="/api/v1")
app.include_router(entries.router, prefix="/api/v1")
app.include_router(extract.router, prefix="/api/v1")
app.include_router(pkm.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(security.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
app.include_router(tags.router, prefix="/api/v1")


def main() -> None:
    """Find a free port, signal Tauri, and start the uvicorn server."""
    setup_logging()
    port = _find_free_port()
    app.state.port = port
    print(f"LISTENING_ON_PORT:{port}", flush=True)
    uv_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None)
    asyncio.run(uvicorn.Server(uv_config).serve())
