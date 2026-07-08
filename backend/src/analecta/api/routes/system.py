import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from importlib.metadata import version

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from analecta.api.deps import get_index
from analecta.api.events import EventBus
from analecta.storage.index import VaultIndex

log = logging.getLogger(__name__)
router = APIRouter()


class RescanOut(BaseModel):
    """Result of a manual vault rescan.

    Attributes:
        updated: Number of entries found out of sync with their Markdown
            file (mtime drift) and reindexed. Every entry in the vault is
            actually reindexed regardless of this count — it reports how
            many needed it, not how many were touched.
    """

    updated: int


@router.get("/system/health")
async def health(request: Request) -> dict[str, object]:
    """Return sidecar health status.

    Args:
        request: Current HTTP request (used to read port from app state).

    Returns:
        JSON with ``status``, ``version``, and ``port`` fields.
    """
    port: int | None = getattr(request.app.state, "port", None)
    return {"status": "ok", "version": version("analecta"), "port": port}


@router.get("/system/events")
async def events(request: Request) -> EventSourceResponse:
    """Stream server-sent events from the internal event bus.

    Clients should reconnect on disconnect. Individual events carry a JSON
    payload in the ``data`` field. Per-subscriber multiplexing is added in B6;
    for now all subscribers share a single queue.

    Args:
        request: Current HTTP request (used to read event bus from app state).

    Returns:
        An SSE stream that yields events until the client disconnects.
    """
    bus: EventBus = request.app.state.event_bus

    async def _gen() -> AsyncGenerator[dict[str, str]]:
        async with bus.subscribe() as q:
            while True:
                event = await q.get()
                yield {"data": json.dumps(event)}

    return EventSourceResponse(_gen())


@router.post("/system/rescan")
async def rescan(index: VaultIndex = Depends(get_index)) -> RescanOut:
    """Manually re-derive backlinks and FTS content for every vault entry.

    Runs the same reconciliation that fires automatically on sidecar
    startup (:meth:`~analecta.storage.index.VaultIndex.reconcile_stale_entries`),
    but unconditionally — every entry is reindexed from its current file,
    regardless of its recorded mtime. Exposed as a user-triggered fallback
    for edits made while the sidecar is already running (the startup sweep
    can't see those) and for tools that preserve or backdate mtime on
    write, which the startup sweep's mtime comparison can't detect either.

    Args:
        index: Injected VaultIndex singleton.

    Returns:
        How many entries were found out of sync and reindexed — not how
        many entries exist in the vault (see
        :meth:`~analecta.storage.index.VaultIndex.reconcile_stale_entries`).
    """
    count = await asyncio.to_thread(index.reconcile_stale_entries, force=True)
    return RescanOut(updated=count)
