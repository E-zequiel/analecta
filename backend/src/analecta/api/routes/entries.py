import asyncio
import json
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from analecta.api.deps import get_index
from analecta.storage.index import (
    EntryRecord,
    GraphEdgeRecord,
    GraphNodeRecord,
    InvalidTagNameError,
    VaultIndex,
)

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
        tags: List of structural tag names (Tags UI-assigned).
        content_tags: List of content hashtags found in this entry's own
            Markdown, regardless of whether any other entry shares them.
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
    content_tags: list[str]
    flags: list[str]


class EntryTitleOut(BaseModel):
    """A minimal id/title pair for client-side title lookups.

    Attributes:
        id: Entry id.
        title: Entry title.
    """

    id: int
    title: str


class BacklinkContextOut(BaseModel):
    """Surrounding text context for a single backlink occurrence.

    Attributes:
        heading: Section heading above the reference, if any.
        pre: Text immediately before the reference.
        highlight: The matched link text as it appears in source.
        post: Text immediately after the reference.
    """

    heading: str | None = None
    pre: str
    highlight: str
    post: str


class BacklinkOut(BaseModel):
    """A single backlink entry.

    Attributes:
        id: Source entry id.
        name: Source entry title.
        context: Surrounding text context for this occurrence.
    """

    id: int
    name: str
    context: BacklinkContextOut


class BacklinksResultOut(BaseModel):
    """Response body for GET /entries/{id}/backlinks.

    Attributes:
        linked: All backlink occurrences pointing to the requested entry.
    """

    linked: list[BacklinkOut]


class OutgoingLinkOut(BaseModel):
    """A single outgoing link entry.

    Attributes:
        id: Target entry id.
        name: Target entry title.
        context: Surrounding text context for this occurrence.
    """

    id: int
    name: str
    context: BacklinkContextOut


class OutgoingLinksResultOut(BaseModel):
    """Response body for GET /entries/{id}/outgoing-links.

    Attributes:
        linked: All outgoing link occurrences from the requested entry.
    """

    linked: list[OutgoingLinkOut]


class HashtagGroupOut(BaseModel):
    """Entries sharing a tag identity with the queried entry, structural or content.

    Attributes:
        hashtag: Tag identity name. Uses the structural tag's display
            casing when one exists anywhere in the vault, otherwise the
            raw lowercase hashtag form.
        entries: Other entries that carry this tag identity, either as a
            structural tag or as a content hashtag.
    """

    hashtag: str
    entries: list[EntryOut]


class HashtagConnectionsOut(BaseModel):
    """Response body for GET /entries/{id}/hashtag-connections.

    Attributes:
        groups: Hashtag groups sorted by hashtag name.
    """

    groups: list[HashtagGroupOut]


class GraphNodeOut(BaseModel):
    """A node in the vault connection graph.

    Attributes:
        node_id: Prefixed stable identifier — ``entry:{int_id}`` or
            ``tag:{normalized}`` (``casefold`` identity, shared by a
            structural tag and a content hashtag of the same name).
        label: Display label (entry title or ``#tagname``).
        kind: Node kind: ``entry`` or ``tag``.
        source_type: Entry source type or ``None`` for virtual tag nodes.
    """

    node_id: str
    label: str
    kind: str
    source_type: str | None


class GraphEdgeOut(BaseModel):
    """A weighted directed edge in the vault connection graph.

    Attributes:
        source: Source node id.
        target: Target node id.
        weight: Number of individual references collapsed into this edge.
    """

    source: str
    target: str
    weight: int


class GraphResultOut(BaseModel):
    """Response body for GET /entries/graph.

    Attributes:
        nodes: All connected nodes (entries + virtual tag nodes).
        edges: All weighted edges between nodes.
    """

    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class SubgraphResultOut(BaseModel):
    """Response body for GET /entries/{id}/subgraph.

    Attributes:
        focus_node_id: Node id of the focal entry (``entry:{int_id}``).
        nodes: All nodes in the 1-hop neighbourhood including the focus node.
        edges: All directed edges within the subgraph.
    """

    focus_node_id: str
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


def graph_node_out(record: GraphNodeRecord) -> GraphNodeOut:
    """Convert a GraphNodeRecord to the API GraphNodeOut model.

    Args:
        record: Storage graph node record.

    Returns:
        Serialisable GraphNodeOut instance.
    """
    return GraphNodeOut(
        node_id=record.node_id,
        label=record.label,
        kind=record.kind,
        source_type=record.source_type,
    )


def graph_edge_out(record: GraphEdgeRecord) -> GraphEdgeOut:
    """Convert a GraphEdgeRecord to the API GraphEdgeOut model.

    Args:
        record: Storage graph edge record.

    Returns:
        Serialisable GraphEdgeOut instance.
    """
    return GraphEdgeOut(
        source=record.source,
        target=record.target,
        weight=record.weight,
    )


def entry_out(record: EntryRecord, content_tags: list[str] | None = None) -> EntryOut:
    """Convert a storage EntryRecord to the API EntryOut model.

    Args:
        record: Row from the entries table (must have a non-None id).
        content_tags: This entry's own content hashtags, if already
            fetched by the caller (e.g. via
            :meth:`VaultIndex.get_content_hashtags_for_entries`).
            Defaults to an empty list.

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
        content_tags=content_tags or [],
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
        fetched = await asyncio.to_thread(index.get_entries_by_ids, ids)
        records: list[EntryRecord] = []
        for entry in fetched:
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
    content_tags_map = await asyncio.to_thread(
        index.get_content_hashtags_for_entries, [r.id for r in records if r.id]
    )
    return [
        entry_out(r, content_tags_map.get(r.id) if r.id is not None else None)
        for r in records
    ]


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
    """Return read-activity metrics for the Collecta dashboard.

    Returns:
        Dict with keys reads_week, reads_month, reads_year.
    """
    return await asyncio.to_thread(index.get_metrics)


@router.get("/entries/graph", response_model=GraphResultOut)
async def get_entries_graph(
    index: VaultIndex = Depends(get_index),
) -> GraphResultOut:
    """Return all connected nodes and weighted edges for the vault graph.

    Entries with no connections at all — neither backlink refs nor a
    structural tag — are excluded. Every hashtag always produces its own
    tag node, whether or not it also resolves to a matching entry title (a
    resolving hashtag additionally gets an entry->entry edge). Wikilinks
    without a matching entry are silently skipped.

    Args:
        index: Injected VaultIndex singleton.

    Returns:
        GraphResultOut with all nodes and edges.
    """
    nodes, edges = await asyncio.to_thread(index.get_graph)
    return GraphResultOut(
        nodes=[graph_node_out(n) for n in nodes],
        edges=[graph_edge_out(e) for e in edges],
    )


@router.get("/entries/titles", response_model=list[EntryTitleOut])
async def list_entry_titles(
    index: VaultIndex = Depends(get_index),
) -> list[EntryTitleOut]:
    """Return a lightweight id/title pair for every entry.

    Used by the frontend to resolve ``[[wikilinks]]`` to a real entry id
    without fetching the full entry list.

    Args:
        index: Injected VaultIndex singleton.

    Returns:
        List of EntryTitleOut ordered by id.
    """
    rows = await asyncio.to_thread(index.get_all_titles)
    return [EntryTitleOut(id=entry_id, title=title) for entry_id, title in rows]


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
    content_tags_map = await asyncio.to_thread(
        index.get_content_hashtags_for_entries, [entry_id]
    )
    return entry_out(record, content_tags_map.get(entry_id))


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
        HTTPException: 404 if the entry does not exist. 400 if
            ``body.tags`` contains a name that isn't a valid bare hashtag
            token and doesn't already exist as a tag identity (see
            :meth:`~analecta.storage.index.VaultIndex.update_tags`).
    """
    record = await asyncio.to_thread(index.get_entry, entry_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    if (new_status := body.status) is not None:
        await asyncio.to_thread(index.update_status, entry_id, new_status)
    if (new_tags := body.tags) is not None:
        try:
            await asyncio.to_thread(index.update_tags, entry_id, new_tags)
        except InvalidTagNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if (new_flags := body.flags) is not None:
        await asyncio.to_thread(index.update_flags, entry_id, new_flags)
    if (fts := body.fts) is not None:
        await asyncio.to_thread(
            index.update_fts_content, entry_id, fts.title, fts.content
        )
        await asyncio.to_thread(index.index_backlinks, entry_id)
    updated = await asyncio.to_thread(index.get_entry, entry_id)
    assert updated is not None
    content_tags_map = await asyncio.to_thread(
        index.get_content_hashtags_for_entries, [entry_id]
    )
    return entry_out(updated, content_tags_map.get(entry_id))


@router.get("/entries/{entry_id}/backlinks", response_model=BacklinksResultOut)
async def get_entry_backlinks(
    entry_id: int,
    index: VaultIndex = Depends(get_index),
) -> BacklinksResultOut:
    """Return all entries that link to *entry_id*.

    Args:
        entry_id: Target entry id.
        index: Injected VaultIndex singleton.

    Returns:
        BacklinksResultOut with a list of backlink occurrences.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    if await asyncio.to_thread(index.get_entry, entry_id) is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    records = await asyncio.to_thread(index.get_backlinks, entry_id)
    return BacklinksResultOut(
        linked=[
            BacklinkOut(
                id=r.source_id,
                name=r.source_title,
                context=BacklinkContextOut(
                    heading=r.heading,
                    pre=r.pre,
                    highlight=r.highlight,
                    post=r.post,
                ),
            )
            for r in records
        ]
    )


@router.get("/entries/{entry_id}/outgoing-links", response_model=OutgoingLinksResultOut)
async def get_entry_outgoing_links(
    entry_id: int,
    index: VaultIndex = Depends(get_index),
) -> OutgoingLinksResultOut:
    """Return all entries that *entry_id* links to.

    Args:
        entry_id: Source entry id.
        index: Injected VaultIndex singleton.

    Returns:
        OutgoingLinksResultOut with a list of outgoing link occurrences.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    if await asyncio.to_thread(index.get_entry, entry_id) is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    records = await asyncio.to_thread(index.get_outgoing_links, entry_id)
    return OutgoingLinksResultOut(
        linked=[
            OutgoingLinkOut(
                id=r.target_id,
                name=r.target_title,
                context=BacklinkContextOut(
                    heading=r.heading,
                    pre=r.pre,
                    highlight=r.highlight,
                    post=r.post,
                ),
            )
            for r in records
        ]
    )


@router.get(
    "/entries/{entry_id}/hashtag-connections", response_model=HashtagConnectionsOut
)
async def get_entry_hashtag_connections(
    entry_id: int,
    index: VaultIndex = Depends(get_index),
) -> HashtagConnectionsOut:
    """Return other entries grouped by shared tag identity, structural or content.

    Gathers every tag identity *entry_id* carries — structural
    (``entry_tags``) or content (its own ``backlink_refs`` hashtags) —
    then finds peer entries carrying each identity through either
    mechanism. Groups are ordered by tag; entries within each group are
    ordered by title.

    Args:
        entry_id: Source entry id.
        index: Injected VaultIndex singleton.

    Returns:
        HashtagConnectionsOut with a list of hashtag groups.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    if await asyncio.to_thread(index.get_entry, entry_id) is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    groups = await asyncio.to_thread(index.get_hashtag_connections, entry_id)
    all_ids = [e.id for g in groups for e in g.entries if e.id]
    content_tags_map = await asyncio.to_thread(
        index.get_content_hashtags_for_entries, all_ids
    )
    return HashtagConnectionsOut(
        groups=[
            HashtagGroupOut(
                hashtag=g.hashtag,
                entries=[
                    entry_out(
                        e, content_tags_map.get(e.id) if e.id is not None else None
                    )
                    for e in g.entries
                ],
            )
            for g in groups
        ]
    )


@router.get("/entries/{entry_id}/subgraph", response_model=SubgraphResultOut)
async def get_entry_subgraph(
    entry_id: int,
    index: VaultIndex = Depends(get_index),
) -> SubgraphResultOut:
    """Return the 1-hop neighbourhood subgraph centred on a focal entry.

    Includes the focus entry, all entries it links to (outlinks), and all
    entries that link to it (inlinks). The focus entry is always present as
    a node even when it has no connections.

    Args:
        entry_id: Focal entry id.
        index: Injected VaultIndex singleton.

    Returns:
        SubgraphResultOut with focus_node_id, nodes, and edges.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    result = await asyncio.to_thread(index.get_subgraph, entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    nodes, edges = result
    return SubgraphResultOut(
        focus_node_id=f"entry:{entry_id}",
        nodes=[graph_node_out(n) for n in nodes],
        edges=[graph_edge_out(e) for e in edges],
    )


@router.get("/entries/{entry_id}/linked", response_model=list[EntryOut])
async def get_linked_entries(
    entry_id: int,
    index: VaultIndex = Depends(get_index),
) -> list[EntryOut]:
    """Return entries explicitly linked via *entry_id*'s frontmatter ``linked`` field.

    Args:
        entry_id: Source entry id.
        index: Injected VaultIndex singleton.

    Returns:
        List of linked entries in frontmatter order.

    Raises:
        HTTPException: 404 if the entry does not exist.
    """
    if await asyncio.to_thread(index.get_entry, entry_id) is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    records = await asyncio.to_thread(index.get_linked_entries, entry_id)
    content_tags_map = await asyncio.to_thread(
        index.get_content_hashtags_for_entries, [r.id for r in records if r.id]
    )
    return [
        entry_out(r, content_tags_map.get(r.id) if r.id is not None else None)
        for r in records
    ]


@router.post("/entries/{source_id}/link/{target_id}", status_code=204)
async def link_entries(
    source_id: int,
    target_id: int,
    index: VaultIndex = Depends(get_index),
) -> None:
    """Create a bidirectional explicit link between two entries.

    Writes the target's title into the source's frontmatter ``linked`` list
    and vice versa, then re-indexes backlinks for both.

    Args:
        source_id: ID of the first entry.
        target_id: ID of the second entry.
        index: Injected VaultIndex singleton.

    Raises:
        HTTPException: 404 if either entry does not exist.
    """
    if await asyncio.to_thread(index.get_entry, source_id) is None:
        raise HTTPException(status_code=404, detail="Source entry not found")
    if await asyncio.to_thread(index.get_entry, target_id) is None:
        raise HTTPException(status_code=404, detail="Target entry not found")
    await asyncio.to_thread(index.add_link, source_id, target_id)


@router.delete("/entries/{source_id}/link/{target_id}", status_code=204)
async def unlink_entries(
    source_id: int,
    target_id: int,
    index: VaultIndex = Depends(get_index),
) -> None:
    """Remove the bidirectional explicit link between two entries.

    Removes the target's title from the source's frontmatter ``linked`` list
    and vice versa, then re-indexes backlinks for both.

    Args:
        source_id: ID of the first entry.
        target_id: ID of the second entry.
        index: Injected VaultIndex singleton.

    Raises:
        HTTPException: 404 if either entry does not exist.
    """
    if await asyncio.to_thread(index.get_entry, source_id) is None:
        raise HTTPException(status_code=404, detail="Source entry not found")
    if await asyncio.to_thread(index.get_entry, target_id) is None:
        raise HTTPException(status_code=404, detail="Target entry not found")
    await asyncio.to_thread(index.remove_link, source_id, target_id)


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
    assets_dir = file_path.parent.parent / "assets" / slug
    await asyncio.to_thread(index.hard_delete, entry_id)
    await asyncio.to_thread(_unlink_if_exists, file_path)
    if assets_dir.is_dir():
        await asyncio.to_thread(shutil.rmtree, assets_dir)
