"""One-time vault maintenance helpers, run at sidecar startup.

Idempotent by construction: each pass rewrites only what still carries the
legacy key and is safe to run repeatedly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from analecta.markdown.frontmatter import generator_actor

_FM_BLOCK = re.compile(r"^(---\n)([\s\S]*?)(\n---\n)", re.MULTILINE)
_STATUS_KEY = re.compile(r"^status:(.*)$", re.MULTILINE)


def rename_frontmatter_status_keys(vault_path: Path) -> int:
    """Rename the legacy ``status:`` frontmatter key to ``read_state:``.

    T1 (read_state migration): every page under ``{vault}/pages/`` whose YAML
    frontmatter carries a top-level ``status:`` key is rewritten in place to
    ``read_state:``. Only the key name changes — values, other keys, key
    order, and the body are preserved byte-for-byte. Files without
    frontmatter (or without the legacy key) are left untouched, making the
    pass idempotent and cheap to run on every startup.

    Args:
        vault_path: Root directory of the local vault.

    Returns:
        Number of files rewritten.
    """
    pages = vault_path / "pages"
    if not pages.is_dir():
        return 0
    changed = 0
    for page in sorted(pages.glob("*.md")):
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _FM_BLOCK.match(text)
        if not m or not _STATUS_KEY.search(m.group(2)):
            continue
        new_fm = _STATUS_KEY.sub(r"read_state:\1", m.group(2))
        page.write_text(
            f"{m.group(1)}{new_fm}{m.group(3)}{text[m.end() :]}", encoding="utf-8"
        )
        changed += 1
    return changed


def backfill_okf_frontmatter(vault_path: Path) -> int:
    """Backfill OKF ``type``/``generated``/``sources`` into legacy pages.

    T2 (native OKF compatibility): every page under ``{vault}/pages/`` whose
    frontmatter lacks a top-level ``type`` key gains ``type: reference``
    (all captures are references — D1), ``generated: {by, at}`` recording
    the pipeline that produced the file (actor ``analecta/<version>``,
    ``at`` = the page's ``created_at``), and a ``sources[]`` entry mapping
    the recorded ``url``/``author``/``published`` provenance. The body is
    never touched, and pages already carrying ``type`` are skipped, so the
    pass is idempotent and cheap on every startup.

    Args:
        vault_path: Root directory of the local vault.

    Returns:
        Number of pages rewritten.
    """
    pages = vault_path / "pages"
    if not pages.is_dir():
        return 0
    changed = 0
    for page in sorted(pages.glob("*.md")):
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _FM_BLOCK.match(text)
        if not m:
            continue
        try:
            loaded = yaml.safe_load(m.group(2))
        except yaml.YAMLError:
            continue
        if not isinstance(loaded, dict) or "type" in loaded:
            continue
        data: dict[str, Any] = {"type": "reference", **loaded}
        created_at = data.get("created_at")
        if isinstance(created_at, str) and created_at:
            data["generated"] = {"by": generator_actor(), "at": created_at}
        sources = _backfill_source_entry(data)
        if sources is not None:
            data["sources"] = sources
        body = yaml.dump(data, allow_unicode=True, sort_keys=False)
        page.write_text(
            f"{m.group(1)}{body}{m.group(3)}{text[m.end() :]}", encoding="utf-8"
        )
        changed += 1
    return changed


def _backfill_source_entry(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Build the ``sources[]`` list from a legacy page's own frontmatter.

    Returns ``None`` when there is no usable ``url`` to point at.
    """
    url = data.get("url")
    if not isinstance(url, str) or not url:
        return None
    source: dict[str, Any] = {"resource": url}
    author = data.get("author")
    if isinstance(author, str) and author:
        source["author"] = author
    published = data.get("published")
    if isinstance(published, str) and "T" in published:
        source["last_modified"] = published
    return [source]
