import asyncio
import logging
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from analecta.api.deps import get_event_bus, get_index, get_vault
from analecta.api.events import EventBus
from analecta.api.routes.entries import EntryOut, entry_out
from analecta.extraction.assets import AssetDownloader
from analecta.extraction.core import ExtractionError, extract
from analecta.markdown.converter import MarkdownConverter
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager

log = logging.getLogger(__name__)
router = APIRouter()


class ExtractIn(BaseModel):
    """Request body for POST /extract.

    Attributes:
        url: URL to fetch, extract, and save.
    """

    url: str


@router.post("/extract", response_model=EntryOut)
async def extract_url(
    body: ExtractIn,
    index: VaultIndex = Depends(get_index),
    vault: VaultManager = Depends(get_vault),
    event_bus: EventBus = Depends(get_event_bus),
) -> EntryOut:
    """Run the full extraction pipeline for a URL and persist the result.

    Steps: extract → download assets → convert to Markdown → write vault file
    → index entry → publish SSE event.

    Args:
        body: Request body with the target URL.
        index: Injected VaultIndex singleton.
        vault: VaultManager for the configured vault path.
        event_bus: SSE event bus for push notifications.

    Returns:
        The newly created entry.

    Raises:
        HTTPException: 422 if extraction fails or the source is unsupported.
        HTTPException: 409 if the URL already exists in the vault.
        HTTPException: 500 if an unexpected error occurs anywhere in the pipeline.
    """
    try:
        await asyncio.to_thread(vault.ensure_dirs)

        created_dt = datetime.now(tz=UTC)
        created_at = created_dt.isoformat()

        try:
            content = await extract(body.url)
        except NotImplementedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        page_path = vault.page_path(content.title, created_dt)
        slug = page_path.stem
        content.html = await AssetDownloader().process(
            content.html, slug, vault.vault_path
        )

        markdown = MarkdownConverter().convert(content, created_at)
        file_path = await asyncio.to_thread(
            vault.write_page, markdown, content.title, created_dt
        )

        entry = EntryRecord(
            title=content.title,
            url=body.url,
            file_path=str(file_path),
            source_type=content.source_type,
            created_at=created_at,
            updated_at=created_at,
        )

        try:
            entry_id = await asyncio.to_thread(index.add_entry, entry)
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409, detail="URL already in vault"
            ) from None

        await asyncio.to_thread(
            index.update_fts_content, entry_id, content.title, markdown
        )
        event_bus.put_nowait({"type": "entry_added", "id": entry_id})

        result = await asyncio.to_thread(index.get_entry, entry_id)
        assert result is not None
        return entry_out(result)

    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Extract pipeline failed for %s", body.url)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
