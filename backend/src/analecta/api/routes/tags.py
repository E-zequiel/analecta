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
        merge: Required (``True``) to proceed when *new_name*'s identity
            already exists as another structural tag — an explicit,
            irreversible merge of two curated tags. Defaults to ``False``,
            which raises a 409 on collision instead of silently merging.
    """

    new_name: str
    merge: bool = False


class TagBodyCountOut(BaseModel):
    """Count of entries whose Markdown body contains a tag as literal ``#hashtag`` text.

    Attributes:
        count: Number of entries.
    """

    count: int


@router.get("/tags", response_model=list[TagOut])
async def list_tags(
    index: VaultIndex = Depends(get_index),
) -> list[TagOut]:
    """Return all tags ordered alphabetically by name (case-insensitive).

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
    """Create a tag, or resolve to an existing identity.

    Args:
        body: Tag name to create. No-ops if a tag with the same
            case-insensitive identity already exists.
        index: Injected VaultIndex singleton.

    Returns:
        The tag as it exists in the database. If a tag with this
        case-insensitive identity already existed, returns its existing
        display name and current structural+hashtag union count instead
        of creating a duplicate — count may be nonzero even for a
        newly-created tag if content hashtags already reference this
        identity.
    """
    name, count = await asyncio.to_thread(index.create_tag, body.name)
    return TagOut(name=name, count=count)


@router.put("/tags/{name}", response_model=TagOut)
async def rename_tag(
    name: str,
    body: TagRenameIn,
    index: VaultIndex = Depends(get_index),
) -> TagOut:
    """Rename a tag globally — updates all affected entries.

    Args:
        name: Current tag name (URL-encoded).
        body: New name, and whether to merge if it collides.
        index: Injected VaultIndex singleton.

    Returns:
        Updated tag — for a merge, ``name`` is the destination's
        preexisting canonical casing, which may differ from
        ``body.new_name``.

    Raises:
        HTTPException: 409 if *new_name* already exists as another
            structural tag and ``body.merge`` isn't ``True``, or if
            body-text occurrences of *name* exist but can't be migrated to
            the resolved destination name (see
            :meth:`~analecta.storage.index.VaultIndex.rename_tag`).
    """
    try:
        result = await asyncio.to_thread(
            index.rename_tag, name, body.new_name, merge=body.merge
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        return TagOut(name=body.new_name, count=0)
    new_name, count = result
    return TagOut(name=new_name, count=count)


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


@router.get("/tags/{name}/body-count", response_model=TagBodyCountOut)
async def get_tag_body_count(
    name: str,
    index: VaultIndex = Depends(get_index),
) -> TagBodyCountOut:
    """Return how many entries contain *name* as literal ``#hashtag`` text.

    Used by the delete-tag confirmation UI to warn that deleting a tag
    doesn't remove these occurrences — they get converted to inline code
    instead. See :meth:`VaultIndex.get_body_hashtag_entry_ids`.

    Args:
        name: Tag name (URL-encoded).
        index: Injected VaultIndex singleton.

    Returns:
        The count.
    """
    ids = await asyncio.to_thread(index.get_body_hashtag_entry_ids, name)
    return TagBodyCountOut(count=len(ids))
