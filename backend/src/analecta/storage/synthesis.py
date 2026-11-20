"""First-class synthesis entries (owner decision D10).

A synthesis is an app entry like any capture — indexed by FTS, backlinks, and
the graph — but its content is bot-produced: it cites the capture entries it
synthesizes via OKF ``sources[]``, carries ``type: synthesis`` in its
frontmatter, and uses the synthetic URL ``analecta://synthesis/<slug>``. The
DB discriminator is ``source_type='synthesis'`` (no migration needed; the
four capture origins are untouched).
"""

from __future__ import annotations

import os
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


MISSING_BASIS = ""
"""Basis recorded for a cited capture whose page is unreadable at
synthesis or update time. It never matches a real body hash, so the
source is reported stale as soon as its page exists and can be read."""


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

    sources, sources_basis = _sources_frontmatter(source_entries)
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


def update_synthesis_entry(
    index: VaultIndex,
    record: EntryRecord,
    *,
    body: str,
    source_entries: list[EntryRecord],
    generated_by: str | None = None,
) -> None:
    """Regenerate an existing synthesis in place.

    Keeps the page path, URL slug, database row id, and title; refreshes
    the body, ``sources[]`` and ``sources_basis[]`` (rewritten together,
    in *source_entries* order, each basis being the cited capture's
    current body hash, D9 convention), and ``generated`` metadata:
    ``generated.at`` is bumped to now and ``generated.by`` is replaced
    only when *generated_by* is given. FTS content and backlink refs are
    re-derived from the rewritten page. The recorded summary is fully
    cleared — frontmatter keys and mirrored DB columns — so a new body
    never lingers with a stale summary, and the DB row's ``updated_at``
    is bumped to now.

    The page is rewritten atomically: the new markdown is written to a
    temporary file in the page's directory and published with a single
    ``os.replace``, so a reader never observes a half-written page. The
    three derived-state mutations (summary clearing with its
    ``updated_at`` bump, FTS content, backlink refs) then land in one
    database transaction. The rename→commit gap is the remaining
    non-atomic step: a crash between them leaves FTS/backlinks derived
    from the previous page revision, which
    :meth:`VaultIndex.reconcile_stale_entries` re-derives from the page.

    When the page's frontmatter parses as a dict it is preserved as the
    base of the rewrite, even if its ``type`` was hand-edited away from
    ``synthesis`` — the DB row already proved the entry is a synthesis;
    only unparseable frontmatter rebuilds from DB defaults. Canonical
    keys (``title``/``url``/``source_type``/``created_at``/``tags``/
    ``read_state``/``generated``/``sources[]``/``sources_basis``) are
    normalized from the DB row and new values (a hand-edited
    non-``synthesis`` ``type`` survives via ``setdefault``), so any
    extra keys the page carried (e.g. ``status``, custom keys) survive
    the rewrite.

    Args:
        index: Vault index singleton (single writer).
        record: The synthesis row to regenerate; its ``file_path`` is
            rewritten at the same path.
        body: New markdown body of the synthesis.
        source_entries: New citation set, in the requested order.
        generated_by: OKF actor id to record in ``generated.by``;
            ``None`` keeps the existing actor.
    """
    now = _now()
    page_path = Path(record.file_path)
    # Any parseable frontmatter dict is preserved as the rewrite base,
    # even with a hand-edited ``type`` — the DB row already proved the
    # entry is a synthesis. Only unparseable frontmatter rebuilds from
    # DB defaults.
    existing = _page_frontmatter(record)
    data: dict[str, object] = dict(existing) if existing is not None else {}
    data.setdefault("type", "synthesis")
    data["title"] = record.title
    data["url"] = record.url
    data["source_type"] = "synthesis"
    if not isinstance(data.get("created_at"), str):
        data["created_at"] = record.created_at
    data.setdefault("tags", [])
    data.setdefault("read_state", record.read_state)
    generated = data.get("generated")
    if not isinstance(generated, dict):
        generated = {}
        data["generated"] = generated
    generated["at"] = now
    if generated_by is not None:
        generated["by"] = generated_by
    elif "by" not in generated:
        generated["by"] = ""
    data["sources"], data["sources_basis"] = _sources_frontmatter(source_entries)
    # The recorded summary never survives a body rewrite — drop the
    # frontmatter keys and null the mirrored DB columns.
    data.pop("summary", None)
    data.pop("summary_basis", None)
    frontmatter = f"---\n{yaml.dump(data, allow_unicode=True, sort_keys=False)}---\n"
    markdown = f"{frontmatter}\n{body}\n"
    # Publish the complete new page through a sibling temp file + atomic
    # rename; a crash mid-write never leaves a truncated page behind.
    tmp_path = page_path.with_name(f".{page_path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(markdown, encoding="utf-8")
        os.replace(tmp_path, page_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    assert record.id is not None
    index.clear_summary_and_reindex(record.id, record.title, markdown)


def _sources_frontmatter(
    source_entries: list[EntryRecord],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Build the ``sources[]`` / ``sources_basis[]`` frontmatter lists.

    Each cited capture contributes one ``sources[]`` entry (its
    vault-relative page resource and title) and one index-aligned
    ``sources_basis[]`` entry whose ``basis`` is the capture's current
    post-frontmatter body hash (D9 convention) at synthesis/update time.
    The two lists share *source_entries* order and are always rewritten
    together, so no cited source is ever left without a verifiable basis.

    Args:
        source_entries: Cited capture entries, in citation order.

    Returns:
        The ``(sources, sources_basis)`` frontmatter lists.
    """
    sources = [
        {"resource": f"/pages/{Path(e.file_path).name}", "title": e.title}
        for e in source_entries
    ]
    sources_basis = [
        {"resource": f"/pages/{Path(e.file_path).name}", "basis": _capture_basis(e)}
        for e in source_entries
    ]
    return sources, sources_basis


def _capture_basis(entry: EntryRecord) -> str:
    """Body hash (D9 convention) of a cited capture page as stored on disk."""
    try:
        text = Path(entry.file_path).read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return MISSING_BASIS
    return body_sha16(text)


def _page_frontmatter(record: EntryRecord) -> dict[str, object] | None:
    """Parse a page's frontmatter dict, ``None`` if unparseable or not a dict.

    Deliberately does not inspect ``type``: callers decide whether the
    page qualifies (e.g. :func:`_synthesis_frontmatter` keeps returning
    ``None`` for non-synthesis pages), while the update path takes the
    raw dict so a hand-edited ``type`` never discards the page's extra
    keys.
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


def _synthesis_frontmatter(record: EntryRecord) -> dict[str, object] | None:
    """Parse the frontmatter of a synthesis page, ``None`` if not one."""
    data = _page_frontmatter(record)
    if data is None or data.get("type") != "synthesis":
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


def _resource_path(record: EntryRecord, resource: str) -> Path | None:
    """Resolve a vault-relative ``/pages/<file>.md`` resource to a path.

    Returns ``None`` when the resolved path escapes the vault's
    ``pages/`` directory (frontmatter-supplied ``..`` segments). Callers
    treat such a resource as stale and must never read the file it
    points at — it lies outside the vault.
    """
    pages_dir = Path(record.file_path).parent
    vault_root = pages_dir.parent if pages_dir.name == "pages" else pages_dir
    resolved = (vault_root / resource.lstrip("/")).resolve()
    if not resolved.is_relative_to(pages_dir.resolve()):
        return None
    return resolved


def synthesis_stale_sources(record: EntryRecord) -> list[str]:
    """Return the cited sources of *record* that are stale.

    A cited source is stale when its page no longer exists, when its current
    post-frontmatter body hash differs from the ``basis`` recorded in the
    synthesis page's ``sources_basis`` frontmatter (the single source of
    truth; never the database), when the resource resolves outside the
    vault's ``pages/`` directory (stale on the resource string alone; the
    outside file is never read), or when it lacks a matching
    ``sources_basis`` entry (legacy or hand-edited frontmatter —
    unverifiable):

    - A nonempty ``sources[]`` with an absent or empty ``sources_basis``
      (legacy or hand-edited frontmatter) is stale wholesale: every cited
      resource is returned, since none is verifiable.
    - A ``sources[]`` entry without a matching ``sources_basis`` resource
      is reported stale; an entry *with* a matching basis keeps the usual
      hash-drift / missing-file decision. A synthesis with no sources at
      all is fresh (nothing to verify).

    ``sources_basis`` entries whose resource does not appear in
    ``sources[]`` are verified too (hash drift and missing file make
    them stale): the check runs over the whole ``sources_basis`` list,
    a superset of the citations in ``sources[]``.

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
        path = _resource_path(record, resource)
        if path is None:
            # Escapes pages/ containment: stale on the resource string
            # alone, without reading the file outside the vault.
            stale.append(resource)
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
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


def synthesis_staleness(record: EntryRecord) -> list[str]:
    """Classify a synthesis's staleness, including its own page's health.

    :func:`synthesis_stale_sources` only verifies the cited sources, so a
    synthesis whose own page is missing, unreadable, or unparseable would
    silently classify as fresh there. This is the full classification
    shared by every staleness surface (the ``synthesize --stale`` report
    and ``index`` marking): when the synthesis's own page cannot be read
    and parsed, the synthesis is unverifiable and therefore stale, named
    by its own page resource (``/pages/<file>.md``); otherwise the
    cited-source verdict of :func:`synthesis_stale_sources` applies.

    Args:
        record: The synthesis entry record to inspect.

    Returns:
        The stale resources to report, in frontmatter order. Empty list
        means fresh.
    """
    if not record.file_path:
        return []
    if _page_frontmatter(record) is None:
        return [f"/pages/{Path(record.file_path).name}"]
    return synthesis_stale_sources(record)


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
