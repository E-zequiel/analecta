import asyncio
import logging
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analecta.api.routes import system
from analecta.config import setup_logging

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
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan — signals sidecar readiness on startup."""
    log.info("sidecar ready")
    print("SIDECAR_READY", flush=True)
    yield
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

app.include_router(system.router, prefix="/api/v1")


def main() -> None:
    """Find a free port, signal Tauri, and start the uvicorn server."""
    setup_logging()
    port = _find_free_port()
    print(f"LISTENING_ON_PORT:{port}", flush=True)
    uv_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None)
    asyncio.run(uvicorn.Server(uv_config).serve())
