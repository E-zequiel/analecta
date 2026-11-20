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

from analecta.markdown.frontmatter import FM_BLOCK, body_sha16
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
    sources_basis = [
        {
            "resource": f"/pages/{Path(e.file_path).name}",
            "basis": _capture_basis(e),
        }
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
        "sources_basis": sources_basis,
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


def _capture_basis(entry: EntryRecord) -> str:
    """Body hash (D9 convention) of a cited capture page as stored on disk."""
    try:
        text = Path(entry.file_path).read_text(encoding="utf-8")
    except OSError:
        return ""
    return body_sha16(text)


def _synthesis_frontmatter(record: EntryRecord) -> dict[str, object] | None:
    """Parse the frontmatter of a synthesis page, ``None`` if not one."""
    try:
        text = Path(record.file_path).read_text(encoding="utf-8")
    except OSError:
        return None
    m = FM_BLOCK.match(text)
    if not m:
        return None
    try:
        data: object = yaml.safe_load(m.group(2))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or data.get("type") != "synthesis":
        return None
    return data


def _synthesis_source_resources(synth: EntryRecord) -> list[str]:
    """Return the ``sources[].resource`` list of a synthesis page."""
    data = _synthesis_frontmatter(synth)
    if data is None:
        return []
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [
        s["resource"]
        for s in sources
        if isinstance(s, dict) and isinstance(s.get("resource"), str)
    ]


def _resource_path(record: EntryRecord, resource: str) -> Path:
    """Resolve a vault-relative ``/pages/<file>.md`` resource to a path."""
    pages_dir = Path(record.file_path).parent
    vault_root = pages_dir.parent if pages_dir.name == "pages" else pages_dir
    return vault_root / resource.lstrip("/")


def synthesis_stale_sources(record: EntryRecord) -> list[str]:
    """Return the cited sources of *record* that are stale (S2, okf-cli).

    A cited source is stale when its page no longer exists, when its current
    post-frontmatter body hash differs from the ``basis`` recorded in the
    synthesis page's ``sources_basis`` frontmatter (the single source of
    truth; never the database), or — per the amended S2c rules — when it
    lacks a matching ``sources_basis`` entry:

    - A nonempty ``sources[]`` with an absent or empty ``sources_basis``
      (legacy or hand-edited frontmatter) is stale wholesale: every cited
      resource is returned, since none is verifiable.
    - A ``sources[]`` entry without a matching ``sources_basis`` resource
      is reported stale; an entry *with* a matching basis keeps the usual
      hash-drift / missing-file decision. A synthesis with no sources at
      all is fresh (nothing to verify).

    Args:
        record: The synthesis entry record to inspect.

    Returns:
        The ``sources[].resource`` strings that are stale, in frontmatter
        order, without duplicates. Empty list means fresh. Non-synthesis
        records or unreadable frontmatter yield an empty list.
    """
    data = _synthesis_frontmatter(record)
    if data is None:
        return []
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        return []
    source_resources = [
        s["resource"]
        for s in sources
        if isinstance(s, dict) and isinstance(s.get("resource"), str)
    ]
    if not source_resources:
        return []
    basis_entries = data.get("sources_basis", [])
    if not isinstance(basis_entries, list):
        basis_entries = []
    basis_map: dict[str, str] = {}
    for item in basis_entries:
        if not isinstance(item, dict):
            continue
        resource = item.get("resource")
        basis = item.get("basis")
        if isinstance(resource, str) and isinstance(basis, str):
            basis_map[resource] = basis
    if not basis_map:
        # Legacy/hand-edited frontmatter: cited but unverifiable wholesale.
        return list(dict.fromkeys(source_resources))
    stale: list[str] = []
    checked: set[str] = set()

    def _check(resource: str, basis: str) -> None:
        checked.add(resource)
        try:
            text = _resource_path(record, resource).read_text(encoding="utf-8")
        except OSError:
            stale.append(resource)
            return
        if body_sha16(text) != basis:
            stale.append(resource)

    # Pairing-driven check: every cited source is decided by its matching
    # basis entry (hash drift / missing file) or reported as uncovered.
    for resource in source_resources:
        if resource in checked:
            continue
        basis = basis_map.get(resource)
        if basis is None:
            checked.add(resource)
            stale.append(resource)
        else:
            _check(resource, basis)
    # Basis entries outside sources[] keep their original staleness check.
    for item in basis_entries:
        if not isinstance(item, dict):
            continue
        resource = item.get("resource")
        basis = item.get("basis")
        if (
            not isinstance(resource, str)
            or not isinstance(basis, str)
            or resource in checked
        ):
            continue
        _check(resource, basis)
    return list(dict.fromkeys(stale))


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
