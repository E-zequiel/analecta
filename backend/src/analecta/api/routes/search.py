import asyncio
import logging

from fastapi import APIRouter, Depends, Query

from analecta.api.deps import get_index
from analecta.api.routes.entries import EntryOut, entry_out
from analecta.storage.index import VaultIndex

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/search", response_model=list[EntryOut])
async def search_entries(
    q: str = Query(min_length=1),
    index: VaultIndex = Depends(get_index),
) -> list[EntryOut]:
    """Full-text search across entry titles and content.

    Dedicated FTS endpoint — use ``GET /entries?q=`` for combined
    listing + search with status / tag filters.

    Args:
        q: FTS5 query string (min 1 character).
        index: Injected VaultIndex singleton.

    Returns:
        Matching entries ordered by BM25 relevance.
    """
    records = await asyncio.to_thread(index.search, q)
    return [entry_out(r) for r in records]
