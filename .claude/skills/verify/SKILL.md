---
name: verify
description: |
  Verify a backend/API change by driving the real FastAPI sidecar over a real
  HTTP socket — not pytest, not TestClient. Use before considering a
  backend change (routes, storage, migrations, startup behavior) done.
  Electron/SvelteKit UI is out of scope here — the user does visual
  testing for that themselves.
---

# Analecta — Runtime Verification

## Scope

This project splits verification by surface:

- **Backend (FastAPI sidecar)**: drivable standalone, covered below.
- **Electron/SvelteKit UI**: the user does visual testing themselves.
  Do not attempt Playwright or browser automation against the Electron
  shell. `mise exec -- ./scripts/check.sh frontend` (svelte-check + build)
  is the ceiling for automated frontend verification.

## Why not just run `/dev`

`analecta.server.main()` (the real entrypoint, `cd backend && mise exec --
uv run python -m analecta`) calls `load_config()` with no arguments, which
always reads `~/.config/analecta/config.toml` — the user's real config,
pointing at their real vault. **Never read or run against that path.**
There is no env var or CLI flag to override it.

## The harness: real socket, scratch vault

Reuse the actual route modules and CORS middleware from `server.py`, but
supply a scratch `AppConfig` instead of `load_config()`. This is
`conftest.py`'s `_build_full_app` pattern, except run under real
`uvicorn` on a real TCP port instead of an ASGI test transport — so it
exercises the real socket layer, real CORS preflight handling, and the
real `VaultIndex.__init__` startup path (migrations + startup reconcile),
none of which a TestClient/ASGITransport request quite replicates.

Write a throwaway launcher into the scratchpad (never commit this):

```python
# scratchpad/verify_server.py
import sys
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from analecta.api.events import EventBus
from analecta.api.routes import config as config_routes
from analecta.api.routes import entries, extract, pkm, search, system, tags
from analecta.config import AppConfig
from analecta.storage.index import VaultIndex

vault_path = sys.argv[1]

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = AppConfig(vault_path=vault_path)
    index = VaultIndex(cfg.vault_path / "analecta.db")
    app.state.config = cfg
    app.state.index = index
    app.state.event_bus = EventBus()
    app.state.port = 8199
    print("SIDECAR_READY", flush=True)
    yield
    index.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://index.html"],
    allow_origin_regex=r"http://localhost(:\d+)?",
    allow_methods=["*"], allow_headers=["*"], allow_credentials=True,
)
for router in (config_routes, entries, extract, pkm, search, system, tags):
    app.include_router(router.router, prefix="/api/v1")

uvicorn.run(app, host="127.0.0.1", port=8199, log_level="info")
```

Run it against a scratch vault (never the real one), in the background,
then drive it with `curl`:

```bash
SCRATCH=<your scratchpad dir>
mkdir -p "$SCRATCH/verify_vault/pages"
# ... write a scratch .md file with frontmatter + body ...
mise exec -- uv run python "$SCRATCH/verify_server.py" "$SCRATCH/verify_vault" > "$SCRATCH/server.log" 2>&1 &
disown
sleep 2
curl -s http://127.0.0.1:8199/api/v1/system/health
curl -s http://127.0.0.1:8199/api/v1/tags
# ... exercise the changed endpoint ...
pkill -f verify_server.py
rm -rf "$SCRATCH/verify_vault" "$SCRATCH/verify_server.py" "$SCRATCH/server.log"
```

To seed an entry without going through `/extract` (which does a real
network fetch), open a second `VaultIndex` against the same db path
directly and call `add_entry()` — close it before the server's own
`VaultIndex` opens, or after, but not concurrently mid-write. WAL mode
lets both connections see each other's committed data.

## Gotchas found while building this

- `starlette.testclient.TestClient.app` isn't typed as `FastAPI` —
  basedpyright flags `.state` access on it (`reportAttributeAccessIssue`).
  Keep a typed reference to the `FastAPI` instance you passed to
  `TestClient(...)` instead of reading it back off `c.app`.
- CORS preflight for a new endpoint: verify with
  `curl -X OPTIONS ... -H "Origin: app://index.html" -H "Access-Control-Request-Method: POST"`
  — this is the exact origin Electron's packaged build sends, and the
  project has a documented history of POST/PATCH/DELETE silently
  returning 400 when this isn't wired into `allow_origins`.
- `reconcile_stale_entries(force=True)` (manual "Rescan vault") always
  reindexes every entry in the vault, unconditionally — so its returned
  count is "total entries in vault," not "entries that were actually
  stale." Don't write frontend copy that implies otherwise.
