import asyncio
import base64
import json
import logging
from collections.abc import AsyncGenerator
from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from analecta.api.events import EventBus

log = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_FONT_SUFFIXES = {".ttf", ".otf"}


class _FontError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


def _read_font_sync(path: str) -> tuple[str, str]:
    """Read a font file synchronously and return (base64_data, mime_type).

    Args:
        path: Filesystem path to a font file.

    Returns:
        Tuple of base64-encoded bytes and the MIME type string.

    Raises:
        _FontError: If the extension is unsupported (400) or the file is
            missing (404).
    """
    resolved = Path(path).resolve()
    if resolved.suffix.lower() not in _ALLOWED_FONT_SUFFIXES:
        raise _FontError(400, "Invalid font file type")
    if not resolved.is_file():
        raise _FontError(404, "Font file not found")
    mime = "font/otf" if resolved.suffix.lower() == ".otf" else "font/ttf"
    return base64.b64encode(resolved.read_bytes()).decode(), mime


@router.get("/system/font")
async def get_font(path: str = Query(...)) -> dict[str, str]:
    """Return a user-supplied font file encoded as base64.

    The frontend uses this to construct a ``@font-face`` data URL without
    requiring Tauri ``fs`` capabilities beyond the vault scope.

    Args:
        path: Absolute filesystem path to a ``.ttf`` or ``.otf`` file.

    Returns:
        JSON with ``data`` (base64 string) and ``mime`` fields.

    Raises:
        HTTPException: 400 if the path is invalid or the extension is not
            an allowed font type; 404 if the file does not exist.
    """
    try:
        data, mime = await asyncio.to_thread(_read_font_sync, path)
    except _FontError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return {"data": data, "mime": mime}


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
