import asyncio
import logging
import os
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analecta.api.events import EventBus
from analecta.api.routes import (
    config,
    entries,
    extract,
    pkm,
    search,
    system,
    tags,
)
from analecta.config import load_config, setup_logging
from analecta.manifest import write_vault_manifest
from analecta.session_security import (
    SessionDiscovery,
    VaultLock,
    generate_session_token,
    session_auth_dependency,
)
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


def _data_dir() -> Path:
    """Return the Analecta data dir (session.json lives there)."""
    return Path.home() / ".local" / "share" / "analecta"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan — initialises singletons and signals sidecar readiness."""
    config = load_config()
    # On first run (no config.toml yet) use an in-memory DB so the sidecar
    # doesn't create the default vault directory before the user chooses one.
    db_path = (
        Path(":memory:") if config.first_run else config.vault_path / "analecta.db"
    )
    # D5: dev opt-out is explicit; production sidecars always publish a token
    # and enforce it. First-run (in-memory DB, no vault yet) also skips.
    insecure_dev = os.environ.get("ANALECTA_INSECURE_DEV") == "1"
    discovery = SessionDiscovery(_data_dir())
    token: str | None = None
    lock_fd: int | None = None
    if not config.first_run and not insecure_dev:
        token = generate_session_token()
        discovery.write(port=_bound_port, token=token, pid=os.getpid())
        lock_fd = VaultLock(config.vault_path).acquire()
        if lock_fd is None:
            discovery.remove()
            raise RuntimeError(
                "vault is locked by another Analecta process (flock held)"
            )
    index = VaultIndex(db_path)
    if not config.first_run:
        from analecta.maintenance import (
            backfill_okf_frontmatter,
            rename_frontmatter_status_keys,
        )

        rename_frontmatter_status_keys(config.vault_path)
        backfill_okf_frontmatter(config.vault_path)
    write_vault_manifest(config.vault_path)
    app.state.config = config
    app.state.index = index
    app.state.event_bus = EventBus()
    app.state.session_token = token
    log.info("sidecar ready")
    print("SIDECAR_READY", flush=True)
    print(f"VAULT_PATH:{config.vault_path}", flush=True)
    if token is not None:
        print(f"SIDECAR_TOKEN:{token}", flush=True)
    yield
    index.close()
    if token is not None:
        discovery.remove()
    VaultLock(config.vault_path).release(lock_fd)
    log.info("sidecar shut down")


app = FastAPI(title="Analecta", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://index.html"],
    allow_origin_regex=r"http://localhost(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

_D5_AUTH = [Depends(session_auth_dependency)]

app.include_router(config.router, prefix="/api/v1", dependencies=_D5_AUTH)
app.include_router(entries.router, prefix="/api/v1", dependencies=_D5_AUTH)
app.include_router(extract.router, prefix="/api/v1", dependencies=_D5_AUTH)
app.include_router(pkm.router, prefix="/api/v1", dependencies=_D5_AUTH)
app.include_router(search.router, prefix="/api/v1", dependencies=_D5_AUTH)
app.include_router(system.router, prefix="/api/v1", dependencies=_D5_AUTH)
app.include_router(tags.router, prefix="/api/v1", dependencies=_D5_AUTH)


_bound_port: int = 0


def main() -> None:
    """Find a free port, signal Electron, and start the uvicorn server."""
    global _bound_port
    setup_logging()
    port = _find_free_port()
    _bound_port = port
    app.state.port = port
    print(f"LISTENING_ON_PORT:{port}", flush=True)
    uv_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None)
    asyncio.run(uvicorn.Server(uv_config).serve())
