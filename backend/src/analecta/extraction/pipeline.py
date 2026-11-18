"""Shared extraction pipeline — the single write path into the vault.

Used by the HTTP route (``POST /extract``) and the CLI standalone mode so
both funnel every write through the same code (D3: one writer, one schema).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from analecta.extraction import extract
from analecta.extraction.assets import AssetDownloader
from analecta.markdown.converter import MarkdownConverter
from analecta.storage.index import EntryRecord

if TYPE_CHECKING:
    from analecta.extraction.core import ExtractedContent
    from analecta.storage.index import VaultIndex
    from analecta.storage.vault import VaultManager


async def extract_to_vault(
    index: VaultIndex,
    vault: VaultManager,
    url: str,
    extract_fn: Callable[[str], Awaitable[ExtractedContent]] | None = None,
) -> EntryRecord:
    """Run the full extraction pipeline for *url* and persist the result.

    Steps: extract → download assets → convert to Markdown → write vault
    file → index entry (+ FTS, backlinks).

    Args:
        index: Vault index singleton (the single writer).
        vault: VaultManager for the configured vault path.
        url: Target URL.
        extract_fn: Extraction coroutine override (tests); defaults to
            :func:`analecta.extraction.extract`.

    Returns:
        The freshly created entry.

    Raises:
        NotImplementedError: Unsupported source type (extractor contract).
        analecta.extraction.core.ExtractionError: Extraction failed.
        sqlite3.IntegrityError: URL already in the vault.
    """
    fn = extract_fn or extract
    await asyncio.to_thread(vault.ensure_dirs)

    created_dt = datetime.now(tz=UTC)
    created_at = created_dt.isoformat()

    content = await fn(url)

    page_path = vault.page_path(content.title, created_dt)
    slug = page_path.stem
    content.html = await AssetDownloader().process(
        content.html,
        slug,
        vault.vault_path,
        base_url=content.url,
    )

    markdown = MarkdownConverter().convert(content, created_at)
    file_path = await asyncio.to_thread(
        vault.write_page, markdown, content.title, created_dt
    )

    entry = EntryRecord(
        title=content.title,
        url=url,
        file_path=str(file_path),
        source_type=content.source_type,
        created_at=created_at,
        updated_at=created_at,
    )
    entry_id = await asyncio.to_thread(index.add_entry, entry)

    await asyncio.to_thread(index.update_fts_content, entry_id, content.title, markdown)
    await asyncio.to_thread(index.index_backlinks, entry_id)

    result = await asyncio.to_thread(index.get_entry, entry_id)
    assert result is not None
    return result
