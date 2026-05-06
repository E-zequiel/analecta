import json
import logging
from collections.abc import AsyncGenerator
from importlib.metadata import version

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from analecta.api.events import EventBus

log = logging.getLogger(__name__)
router = APIRouter()


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

    async def _gen() -> AsyncGenerator[dict[str, str], None]:
        async with bus.subscribe() as q:
            while True:
                event = await q.get()
                yield {"data": json.dumps(event)}

    return EventSourceResponse(_gen())
