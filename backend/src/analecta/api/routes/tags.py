import asyncio
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from analecta.api.deps import get_index
from analecta.storage.index import VaultIndex

log = logging.getLogger(__name__)
router = APIRouter()


class TagOut(BaseModel):
    """Tag with its associated entry count.

    Attributes:
        name: Tag name string.
        count: Number of entries currently carrying this tag.
    """

    name: str
    count: int


@router.get("/tags", response_model=list[TagOut])
async def list_tags(
    index: VaultIndex = Depends(get_index),
) -> list[TagOut]:
    """Return all tags ordered by entry count descending.

    Args:
        index: Injected VaultIndex singleton.

    Returns:
        List of tags with counts.
    """
    pairs = await asyncio.to_thread(index.list_tags)
    return [TagOut(name=name, count=count) for name, count in pairs]
