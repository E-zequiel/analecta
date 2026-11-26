"""Shared extraction pipeline — the single write path into the vault.

Used by the HTTP route (``POST /extract``) and the CLI standalone mode so
both funnel every write through the same code (D3: one writer, one schema).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from analecta.extraction import extract
from analecta.extraction.assets import AssetDownloader
from analecta.markdown.converter import MarkdownConverter
from analecta.markdown.frontmatter import FM_BLOCK
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


def _page_frontmatter(record: EntryRecord) -> dict[str, object] | None:
    """Parse a page's frontmatter dict, ``None`` if unreadable or not one.

    Local mirror of the storage-side frontmatter parse (kept local: the
    storage helpers are private to their module, the same precedent as
    the CLI's ``_synthesis_frontmatter_data``).
    """
    try:
        text = Path(record.file_path).read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    m = FM_BLOCK.match(text)
    if not m:
        return None
    try:
        data: object = yaml.safe_load(m.group(2))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _cited_resources(data: dict[str, object]) -> list[str]:
    """Vault-relative resources cited by a synthesis's frontmatter.

    Collects the ``resource`` string of every entry of both
    ``sources[]`` and ``sources_basis[]`` (A-T4-5 checks both lists;
    ``sources_basis`` is a superset of the citations in ``sources[]``).
    """
    out: list[str] = []
    for key in ("sources", "sources_basis"):
        cited = data.get(key)
        if not isinstance(cited, list):
            continue
        out += [
            item["resource"]
            for item in cited
            if isinstance(item, dict) and isinstance(item.get("resource"), str)
        ]
    return out


def deprecation_collision_warnings(index: VaultIndex, file_path: str) -> list[str]:
    """Warn on a re-extraction reusing a filename a deprecated synthesis cites.

    S9. A same-day re-extraction of a deleted capture can land on the same
    ``YYYY-MM-DD-`` filename the old capture had. Deprecated syntheses
    citing that path (their frontmatter ``sources[]``/``sources_basis``
    resource) keep pointing at the new entry's file while staying stale
    (S10) and deprecated (S6 — revival is never automatic), so the
    person is warned. The re-extraction itself is a non-error condition:
    nothing is un-deprecated and no automatic revival happens.

    Args:
        index: Vault index singleton (single writer).
        file_path: Absolute path of the freshly written entry.

    Returns:
        One warning per citing deprecated synthesis, each naming the
        collision filename and the citing synthesis title. Empty when no
        deprecated synthesis cites the filename.
    """
    filename = Path(file_path).name
    warnings: list[str] = []
    for record in index.list_entries(source_type="synthesis"):
        data = _page_frontmatter(record)
        # Frontmatter truth (S2): the derived ``deprecated`` flag mirrors
        # ``status: deprecated``; parsing the page keeps this independent
        # of flag staleness.
        if data is None or data.get("status") != "deprecated":
            continue
        if not any(Path(r).name == filename for r in _cited_resources(data)):
            continue
        warnings.append(
            f"re-extraction reused filename '{filename}', cited by deprecated"
            f" synthesis '{record.title}': its path citation stays stale"
            " until deliberate revival; nothing was un-deprecated"
        )
    return warnings
