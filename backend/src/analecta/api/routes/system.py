import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from analecta.api.deps import get_config, get_event_bus, get_index
from analecta.api.events import EventBus
from analecta.config import AppConfig
from analecta.extraction.assets import AssetDownloader
from analecta.storage.index import EntryRecord, VaultIndex

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


class LocalizeImagesOut(BaseModel):
    """Result of a manual remote-image localization backfill.

    Attributes:
        updated: Number of entries whose Markdown file was rewritten.
        placeholders: Of those, how many rewrites replaced at least one
            image with the bundled local placeholder rather than a
            successful re-download (i.e. the download failed even after
            its retry) — surfaced separately so a run that mostly hits
            placeholders (e.g. a rate-limited CDN) is visible, not folded
            into an opaque "updated N" success.
    """

    updated: int
    placeholders: int


def _read_markdown_if_exists(file_path: Path) -> str | None:
    """Return *file_path*'s text content, or ``None`` if it doesn't exist.

    Args:
        file_path: Path to the entry's Markdown file.

    Returns:
        File content, or ``None`` if the file no longer exists on disk.
    """
    if not file_path.exists():
        return None
    return file_path.read_text(encoding="utf-8")


def _persist_localized_entry(
    index: VaultIndex, entry: EntryRecord, file_path: Path, rewritten: str
) -> None:
    """Write *rewritten* to *file_path* and resync backlinks/FTS for *entry*.

    Args:
        index: VaultIndex to resync.
        entry: The entry being rewritten (must have a persisted id).
        file_path: Path to the entry's Markdown file.
        rewritten: New file content to write.
    """
    file_path.write_text(rewritten, encoding="utf-8")
    assert entry.id is not None
    index.update_fts_content(entry.id, entry.title, rewritten)
    index.index_backlinks(entry.id)


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
async def rescan(
    index: VaultIndex = Depends(get_index),
    event_bus: EventBus = Depends(get_event_bus),
) -> RescanOut:
    """Manually re-derive backlinks and FTS content for every vault entry.

    Runs the same reconciliation that fires automatically on sidecar
    startup (:meth:`~analecta.storage.index.VaultIndex.reconcile_stale_entries`),
    but unconditionally — every entry is reindexed from its current file,
    regardless of its recorded mtime. Exposed as a user-triggered fallback
    for edits made while the sidecar is already running (the startup sweep
    can't see those) and for tools that preserve or backdate mtime on
    write, which the startup sweep's mtime comparison can't detect either.

    Publishes a ``vault_rescanned`` SSE event so already-open windows pick
    up the change — a bulk reconciliation like this has no single changed
    entry to report, so the frontend can't infer it happened the way it
    does for a direct in-app edit, and would otherwise show stale tags/
    links/search until the next manual refresh or restart.

    Args:
        index: Injected VaultIndex singleton.
        event_bus: Injected SSE event bus.

    Returns:
        How many entries were found out of sync and reindexed — not how
        many entries exist in the vault (see
        :meth:`~analecta.storage.index.VaultIndex.reconcile_stale_entries`).
    """
    count = await asyncio.to_thread(index.reconcile_stale_entries, force=True)
    event_bus.put_nowait({"type": "vault_rescanned"})
    return RescanOut(updated=count)


@router.post("/system/localize-images")
async def localize_images(
    index: VaultIndex = Depends(get_index),
    config: AppConfig = Depends(get_config),
    event_bus: EventBus = Depends(get_event_bus),
) -> LocalizeImagesOut:
    """Backfill already-saved entries that still hold a live remote image URL.

    Manual counterpart to :meth:`~analecta.extraction.assets.AssetDownloader.process`,
    which only runs against fresh extractions. Scans every vault entry's
    saved Markdown for a ``![alt](url)`` reference that was never
    localized — the residual gap that predates this class falling back to
    a local placeholder on download failure — and re-downloads (or
    placeholders) each one via
    :meth:`~analecta.extraction.assets.AssetDownloader.localize_markdown`.

    Deliberately a separate action from :func:`rescan`, not folded into
    it: ``/system/rescan`` only re-derives backlinks/FTS from a file as-is
    (read-only w.r.t. the file itself); this endpoint rewrites the file,
    a heavier operation that warrants its own explicit trigger and result
    rather than silently changing what "Rescan" means.

    Publishes the same ``vault_rescanned`` SSE event :func:`rescan` does —
    already-open viewers already treat it as "a file changed outside the
    normal edit path, re-read it," which applies here too.

    Args:
        index: Injected VaultIndex singleton.
        config: Injected AppConfig (for ``vault_path``).
        event_bus: Injected SSE event bus.

    Returns:
        How many entries were rewritten, and how many of those rewrites
        fell back to the local placeholder for at least one image.
    """
    downloader = AssetDownloader()
    updated = 0
    placeholders = 0
    for entry in await asyncio.to_thread(index.list_entries):
        file_path = Path(entry.file_path)
        markdown = await asyncio.to_thread(_read_markdown_if_exists, file_path)
        if markdown is None:
            continue
        rewritten, changed, placeholder_count = await downloader.localize_markdown(
            markdown,
            slug=file_path.stem,
            vault_path=config.vault_path,
            base_url=entry.url,
        )
        if not changed:
            continue

        await asyncio.to_thread(
            _persist_localized_entry, index, entry, file_path, rewritten
        )
        updated += 1
        if placeholder_count:
            placeholders += 1

    if updated:
        event_bus.put_nowait({"type": "vault_rescanned"})
    return LocalizeImagesOut(updated=updated, placeholders=placeholders)
