import asyncio
import base64
import json
import logging
from collections.abc import AsyncGenerator
from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from analecta.api.deps import get_config
from analecta.api.events import EventBus
from analecta.config import AppConfig

log = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_FONT_SUFFIXES = {".ttf", ".otf"}


class _FontError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


def _read_font_sync(path: str, allowed_path: str | None) -> tuple[str, str]:
    """Read a font file synchronously and return (base64_data, mime_type).

    Only the path stored in ``config.custom_font_path`` is served. Any other
    path — including paths that differ only in traversal sequences — is
    rejected with a 403 before the extension or existence checks run.

    Args:
        path: Filesystem path to a font file supplied by the caller.
        allowed_path: Value of ``AppConfig.custom_font_path``; ``None`` if no
            custom font has been configured.

    Returns:
        Tuple of base64-encoded bytes and the MIME type string.

    Raises:
        _FontError: 403 if *path* does not match *allowed_path*; 400 if the
            extension is unsupported; 404 if the file is missing.
    """
    resolved = Path(path).resolve()
    if allowed_path is None or resolved != Path(allowed_path).resolve():
        raise _FontError(403, "Font path not authorised")
    if resolved.suffix.lower() not in _ALLOWED_FONT_SUFFIXES:
        raise _FontError(400, "Invalid font file type")
    if not resolved.is_file():
        raise _FontError(404, "Font file not found")
    mime = "font/otf" if resolved.suffix.lower() == ".otf" else "font/ttf"
    return base64.b64encode(resolved.read_bytes()).decode(), mime


@router.get("/system/font")
async def get_font(
    path: str = Query(...),
    config: AppConfig = Depends(get_config),
) -> dict[str, str]:
    """Return the configured custom font file encoded as base64.

    The frontend uses this to construct a ``@font-face`` data URL when the
    user selects a custom font via the file picker in Settings. Only the path
    stored in ``config.custom_font_path`` is served; all other paths return
    HTTP 403 regardless of whether the file exists.

    Args:
        path: Absolute filesystem path to a ``.ttf`` or ``.otf`` file.
        config: Injected application configuration.

    Returns:
        JSON with ``data`` (base64 string) and ``mime`` fields.

    Raises:
        HTTPException: 403 if *path* does not match the configured
            ``custom_font_path``; 400 if the extension is not an allowed
            font type; 404 if the file does not exist.
    """
    try:
        data, mime = await asyncio.to_thread(
            _read_font_sync, path, config.custom_font_path
        )
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
