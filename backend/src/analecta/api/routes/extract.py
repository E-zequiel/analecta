import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from analecta.api.deps import get_event_bus, get_index, get_vault
from analecta.api.events import EventBus
from analecta.api.routes.entries import EntryOut, entry_out
from analecta.extraction.core import ExtractionError
from analecta.extraction.pipeline import (
    deprecation_collision_warnings,
    extract_to_vault,
)
from analecta.storage.index import VaultIndex
from analecta.storage.vault import VaultManager

log = logging.getLogger(__name__)
router = APIRouter()


class ExtractIn(BaseModel):
    """Request body for POST /extract.

    Attributes:
        url: URL to fetch, extract, and save.
    """

    url: str


class ExtractOut(EntryOut):
    """Serialised extract response: EntryOut plus the additive S9 signal.

    Attributes:
        warnings: Filename-collision warnings (S9), always present — one
            per deprecated synthesis citing the freshly written entry's
            filename, empty when there is no collision. Additive and
            non-error: the entry itself is the plain EntryOut shape.
    """

    warnings: list[str]


@router.post("/extract", response_model=ExtractOut)
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
        The newly created entry, plus the additive ``warnings`` field
        (S9): one string per deprecated synthesis citing the freshly
        written filename, empty when there is no collision.

    Raises:
        HTTPException: 422 if extraction fails or the source is unsupported.
        HTTPException: 409 if the URL already exists in the vault.
        HTTPException: 500 if an unexpected error occurs anywhere in the pipeline.
    """
    try:
        record = await extract_to_vault(index, vault, body.url)
        warnings = await asyncio.to_thread(
            deprecation_collision_warnings, index, record.file_path
        )
        event_bus.put_nowait({"type": "entry_added", "id": record.id})
        return ExtractOut(**entry_out(record).model_dump(), warnings=warnings)

    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="URL already in vault") from None
    except NotImplementedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Extract pipeline failed for %s", body.url)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
