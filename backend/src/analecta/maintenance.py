"""One-time vault maintenance helpers, run at sidecar startup.

Idempotent by construction: each pass rewrites only what still carries the
legacy key and is safe to run repeatedly.
"""

from __future__ import annotations

import re
from pathlib import Path

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
