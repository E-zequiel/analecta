import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
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


class TagCreateIn(BaseModel):
    """Body for POST /tags.

    Attributes:
        name: Tag name to create.
    """

    name: str


class TagRenameIn(BaseModel):
    """Body for PUT /tags/{name}.

    Attributes:
        new_name: Replacement tag name.
    """

    new_name: str


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


@router.post("/tags", response_model=TagOut, status_code=201)
async def create_tag(
    body: TagCreateIn,
    index: VaultIndex = Depends(get_index),
) -> TagOut:
    """Create a standalone tag with no entries.

    Args:
        body: Tag name to create.
        index: Injected VaultIndex singleton.

    Returns:
        The tag as it exists in the database (count=0 if newly created).
    """
    await asyncio.to_thread(index.create_tag, body.name)
    pairs = await asyncio.to_thread(index.list_tags)
    for name, count in pairs:
        if name == body.name:
            return TagOut(name=name, count=count)
    return TagOut(name=body.name, count=0)


@router.put("/tags/{name}", response_model=TagOut)
async def rename_tag(
    name: str,
    body: TagRenameIn,
    index: VaultIndex = Depends(get_index),
) -> TagOut:
    """Rename a tag globally — updates all affected entries.

    Args:
        name: Current tag name (URL-encoded).
        body: New name.
        index: Injected VaultIndex singleton.

    Returns:
        Updated tag.

    Raises:
        HTTPException: 409 if *new_name* already exists.
    """
    try:
        await asyncio.to_thread(index.rename_tag, name, body.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    pairs = await asyncio.to_thread(index.list_tags)
    for n, count in pairs:
        if n == body.new_name:
            return TagOut(name=n, count=count)
    return TagOut(name=body.new_name, count=0)


@router.delete("/tags/{name}", status_code=204)
async def delete_tag(
    name: str,
    index: VaultIndex = Depends(get_index),
) -> None:
    """Delete a tag globally — removes it from all entries.

    Args:
        name: Tag name to delete (URL-encoded).
        index: Injected VaultIndex singleton.
    """
    await asyncio.to_thread(index.delete_tag, name)
