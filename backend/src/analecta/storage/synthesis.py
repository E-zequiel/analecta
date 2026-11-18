"""First-class synthesis entries (owner decision D10).

A synthesis is an app entry like any capture — indexed by FTS, backlinks, and
the graph — but its content is bot-produced: it cites the capture entries it
synthesizes via OKF ``sources[]``, carries ``type: synthesis`` in its
frontmatter, and uses the synthetic URL ``analecta://synthesis/<slug>``. The
DB discriminator is ``source_type='synthesis'`` (no migration needed; the
four capture origins are untouched).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from analecta.markdown.frontmatter import FM_BLOCK
from analecta.storage.index import EntryRecord
from analecta.storage.vault import VaultManager

if TYPE_CHECKING:
    from analecta.storage.index import VaultIndex


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def create_synthesis_entry(
    index: VaultIndex,
    vault: VaultManager,
    *,
    title: str,
    body: str,
    source_entries: list[EntryRecord],
    generated_by: str,
    created_at: str | None = None,
) -> int:
    """Create a first-class synthesis entry (file + DB row + indexes).

    Args:
        index: Vault index singleton (single writer).
        vault: Vault manager used to write the page.
        title: Human-readable synthesis title; also drives the filename slug.
        body: Markdown body of the synthesis (wikilinks/hashtags allowed).
        source_entries: Capture entries this synthesis derives from; each
            becomes an OKF ``sources[]`` entry pointing at the capture's
            vault-relative page path.
        generated_by: OKF actor convention id of the producing bot
            (``<producer>/<version>`` or ``process:<id>``).
        created_at: Creation timestamp (ISO 8601); defaults to now.

    Returns:
        The new entry's database row id.

    Raises:
        sqlite3.IntegrityError: If the synthesis slug already exists
            (``entries.url`` UNIQUE).
    """
    created = created_at or _now()
    created_dt = datetime.fromisoformat(created)
    page_path = vault.page_path(title, created_dt)
    # The URL slug has no date prefix; the filename does (vault convention).
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", page_path.stem)
    url = f"analecta://synthesis/{slug}"

    sources = [
        {"resource": f"/pages/{Path(e.file_path).name}", "title": e.title}
        for e in source_entries
    ]
    data: dict[str, object] = {
        "type": "synthesis",
        "title": title,
        "url": url,
        "source_type": "synthesis",
        "created_at": created,
        "tags": [],
        "read_state": "unread",
        "generated": {"by": generated_by, "at": created},
        "sources": sources,
    }
    frontmatter = f"---\n{yaml.dump(data, allow_unicode=True, sort_keys=False)}---\n"
    markdown = f"{frontmatter}\n{body}\n"
    vault.write_page(markdown, title, created_dt)

    entry = EntryRecord(
        title=title,
        url=url,
        file_path=str(page_path),
        source_type="synthesis",
        created_at=created,
        updated_at=created,
    )
    eid = index.add_entry(entry)
    index.update_fts_content(eid, title, markdown)
    index.index_backlinks(eid)
    return eid


def _synthesis_source_resources(synth: EntryRecord) -> list[str]:
    """Return the ``sources[].resource`` list of a synthesis page."""
    try:
        text = Path(synth.file_path).read_text(encoding="utf-8")
    except OSError:
        return []
    m = FM_BLOCK.match(text)
    if not m:
        return []
    try:
        data: object = yaml.safe_load(m.group(2))
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict) or data.get("type") != "synthesis":
        return []
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [
        s["resource"]
        for s in sources
        if isinstance(s, dict) and isinstance(s.get("resource"), str)
    ]


def find_citing_syntheses(index: VaultIndex, entry: EntryRecord) -> list[EntryRecord]:
    """Find synthesis entries whose ``sources[]`` cite *entry*'s page.

    Matching is by vault-relative page resource (``/pages/<file>.md``), the
    same shape ``create_synthesis_entry`` writes.

    Args:
        index: Vault index singleton.
        entry: The entry whose citations are searched.

    Returns:
        The synthesis ``EntryRecord`` objects citing it.
    """
    target = f"/pages/{Path(entry.file_path).name}"
    out: list[EntryRecord] = []
    for record in index.list_entries():
        if record.source_type != "synthesis" or record.id == entry.id:
            continue
        if target in _synthesis_source_resources(record):
            out.append(record)
    return out


def delete_syntheses_for_entry(index: VaultIndex, entry: EntryRecord) -> None:
    """Delete syntheses citing *entry*, in cascade (owner decision D13).

    Unidirectional by construction: a synthesis is never cited by another
    synthesis in v1 (single-source), so passing a synthesis is a no-op —
    deleting one never touches its capture.
    """
    for synth in find_citing_syntheses(index, entry):
        if synth.id is None:
            continue
        index.hard_delete(synth.id)
        try:
            Path(synth.file_path).unlink(missing_ok=True)
        except OSError:
            pass
