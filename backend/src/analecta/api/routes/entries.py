import asyncio
import json
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from analecta.api.deps import get_index
from analecta.storage.index import EntryRecord, VaultIndex

log = logging.getLogger(__name__)
router = APIRouter()


def _unlink_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FtsPatch(BaseModel):
    """FTS content to reindex alongside an entry update.

    Attributes:
        title: New title for the FTS index.
        content: Full plain-text body for the FTS index.
    """

    title: str
    content: str


class EntryPatchIn(BaseModel):
    """Partial-update body for PATCH /entries/{id}.

    Attributes:
        status: New status value, if provided.
        tags: Replacement tag list, if provided.
        flags: Replacement flags list, if provided.
        fts: FTS content to reindex, if provided.
    """

    status: str | None = None
    tags: list[str] | None = None
    flags: list[str] | None = None
    fts: FtsPatch | None = None


class EntryOut(BaseModel):
    """Serialised entry returned by the API.

    Attributes:
        id: Database row id.
        title: Article title.
        url: Source URL.
        file_path: Absolute path to the vault Markdown file.
        source_type: One of article / youtube / substack / x.
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last-update timestamp.
        status: Entry status string.
        tags: List of tag names.
        flags: List of flag strings (e.g. bookmark, gem).
    """

    id: int
    title: str
    url: str
    file_path: str
    source_type: str
    created_at: str
    updated_at: str
    status: str
    tags: list[str]
    flags: list[str]


def entry_out(record: EntryRecord) -> EntryOut:
    """Convert a storage EntryRecord to the API EntryOut model.

    Args:
        record: Row from the entries table (must have a non-None id).

    Returns:
        Serialisable EntryOut instance.
    """
    assert record.id is not None
    return EntryOut(
        id=record.id,
        title=record.title,
        url=record.url,
        file_path=record.file_path,
        source_type=record.source_type,
        created_at=record.created_at,
        updated_at=record.updated_at,
        status=record.status,
        tags=json.loads(record.tags_json),
        flags=json.loads(record.flags_json),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/entries", response_model=list[EntryOut])
async def list_entries(
    status: str | None = None,
    flag: str | None = None,
    exclude_flag: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    index: VaultIndex = Depends(get_index),
) -> list[EntryOut]:
    """List entries with optional filters and sort control.

    When *q* is present, delegates to FTS5 search (BM25 relevance order,
    sort_by/sort_dir ignored). Otherwise lists by status / flag / tag.
    All filters compose.

    Args:
        status: Optional status filter (unread / read).
        flag: Optional flag filter (bookmark / gem / archive).
        exclude_flag: Optional flag exclusion — omits entries containing this flag
            (e.g. ``archive`` to hide archived entries from the library view).
        tag: Optional tag name filter.
        q: Optional FTS5 query string.
        sort_by: Column to sort by — ``title`` or ``created_at`` (default).
        sort_dir: Sort direction — ``asc`` or ``desc`` (default).
        index: Injected VaultIndex singleton.

    Returns:
        List of matching entries ordered by relevance or the specified sort.
    """
    if q:
        records = await asyncio.to_thread(index.search, q)
        if status:
            records = [r for r in records if r.status == status]
        if flag:
            records = [r for r in records if flag in json.loads(r.flags_json)]
        if exclude_flag:
            records = [
                r for r in records if exclude_flag not in json.loads(r.flags_json)
            ]
        if tag:
            tag_ids = set(await asyncio.to_thread(index.get_entry_ids_by_tag, tag))
            records = [r for r in records if r.id in tag_ids]
    elif tag:
        ids = await asyncio.to_thread(index.get_entry_ids_by_tag, tag)
        records: list[EntryRecord] = []
        for eid in ids:
            entry = await asyncio.to_thread(index.get_entry, eid)
            if entry is None:
                continue
            if status is not None and entry.status != status:
                continue
            if flag is not None and flag not in json.loads(entry.flags_json):
                continue
            if exclude_flag is not None and exclude_flag in json.loads(
                entry.flags_json
            ):
                continue
            records.append(entry)
        reverse = sort_dir.lower() == "desc"
        if sort_by == "title":
            records.sort(key=lambda r: r.title.lower(), reverse=reverse)
        else:
            records.sort(key=lambda r: r.created_at, reverse=reverse)
    else:
        records = await asyncio.to_thread(
            index.list_entries,
            status=status,
            flag=flag,
            exclude_flag=exclude_flag,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    return [entry_out(r) for r in records]


@router.get("/entries/counts", response_model=dict[str, int])
async def get_entry_counts(
    index: VaultIndex = Depends(get_index),
) -> dict[str, int]:
    """Return entry counts for all dashboard sections in one aggregated query.

    Returns:
        Dict with keys library, unread, read, bookmark, gem, archive.
    """
    return await asyncio.to_thread(index.get_counts)


@router.get("/entries/metrics", response_model=dict[str, int])
async def get_entry_metrics(
    index: VaultIndex = Depends(get_index),
) -> dict[str, int]:
    """Return read-activity metrics for the Collectio dashboard.

    Returns:
        Dict with keys reads_week, reads_month, reads_year.
    """
    return await asyncio.to_thread(index.get_metrics)


@router.get("/entries/{entry_id}", response_model=EntryOut)
async def get_entry(
    entry_id: int,
    index: VaultIndex = Depends(get_index),
) -> EntryOut:
    """Fetch a single entry by id.

    Args:
        entry_id: Database row id.
        index: Injected VaultIndex singleton.

    Returns:
        The matching entry.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    record = await asyncio.to_thread(index.get_entry, entry_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry_out(record)


@router.patch("/entries/{entry_id}", response_model=EntryOut)
async def patch_entry(
    entry_id: int,
    body: EntryPatchIn,
    index: VaultIndex = Depends(get_index),
) -> EntryOut:
    """Partially update an entry's status, tags, and/or FTS content.

    Args:
        entry_id: Database row id.
        body: Fields to update (all optional).
        index: Injected VaultIndex singleton.

    Returns:
        The updated entry.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    record = await asyncio.to_thread(index.get_entry, entry_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    if (new_status := body.status) is not None:
        await asyncio.to_thread(index.update_status, entry_id, new_status)
    if (new_tags := body.tags) is not None:
        await asyncio.to_thread(index.update_tags, entry_id, new_tags)
    if (new_flags := body.flags) is not None:
        await asyncio.to_thread(index.update_flags, entry_id, new_flags)
    if (fts := body.fts) is not None:
        await asyncio.to_thread(
            index.update_fts_content, entry_id, fts.title, fts.content
        )
    updated = await asyncio.to_thread(index.get_entry, entry_id)
    assert updated is not None
    return entry_out(updated)


@router.delete("/entries/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: int,
    index: VaultIndex = Depends(get_index),
) -> None:
    """Permanently delete an entry from the database and remove its vault file.

    Args:
        entry_id: Database row id.
        index: Injected VaultIndex singleton.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    record = await asyncio.to_thread(index.get_entry, entry_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    file_path = Path(record.file_path)
    slug = file_path.stem
    assets_dir = file_path.parent / "assets" / slug
    await asyncio.to_thread(index.hard_delete, entry_id)
    await asyncio.to_thread(_unlink_if_exists, file_path)
    if assets_dir.is_dir():
        await asyncio.to_thread(shutil.rmtree, assets_dir)
