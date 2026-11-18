"""YAML frontmatter builder — M4 pipeline."""

import importlib.metadata
import re
from typing import Any

import yaml

from analecta.extraction.core import ExtractedContent


def generator_actor() -> str:
    """Return the OKF actor convention id for this pipeline version."""
    try:
        version = importlib.metadata.version("analecta")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - dev tree
        version = "dev"
    return f"analecta/{version}"


FM_BLOCK = re.compile(r"^(---\n)([\s\S]*?)(\n---\n)", re.MULTILINE)


def update_linked(
    markdown: str, *, add: str | None = None, remove: str | None = None
) -> str:
    """Add or remove a title from the ``linked`` frontmatter field.

    Creates the field when adding to a file that lacks it. Removes the field
    entirely when the resulting list is empty.

    Args:
        markdown: Raw Markdown text with YAML frontmatter.
        add: Title to append to the linked list (if not already present).
        remove: Title to remove from the linked list (if present).

    Returns:
        Modified Markdown string, or the original if no frontmatter found.
    """
    m = FM_BLOCK.match(markdown)
    if not m:
        return markdown

    yaml_str = m.group(2)
    rest = markdown[m.end() :]

    try:
        data: dict[str, Any] = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError:
        return markdown

    linked: list[str] = list(data.get("linked") or [])

    if add and add not in linked:
        linked.append(add)
    if remove and remove in linked:
        linked.remove(remove)

    if linked:
        data["linked"] = linked
    elif "linked" in data:
        del data["linked"]

    new_yaml = yaml.dump(data, allow_unicode=True, sort_keys=False)
    return f"---\n{new_yaml}---\n{rest}"


def _source_entry(content: ExtractedContent) -> dict[str, object]:
    """Build the OKF ``sources[]`` entry for an extraction.

    ``resource`` is the source URL. ``author`` is the recorded byline.
    ``last_modified`` carries ``published`` only when it is a full ISO 8601
    datetime with a ``T`` separator — OKF §5.1 requires an explicit datetime
    for timestamp-valued keys, and the article convention records a bare
    date, which must not masquerade as one (D1).
    """
    source: dict[str, object] = {"resource": content.url}
    author = content.metadata.get("author")
    if author:
        source["author"] = author
    published = content.metadata.get("published")
    if isinstance(published, str) and "T" in published:
        source["last_modified"] = published
    return source


def build_frontmatter(content: ExtractedContent, created_at: str) -> str:
    """Build a YAML frontmatter block for *content*.

    Args:
        content: Extracted content whose metadata populates the frontmatter.
        created_at: ISO 8601 creation timestamp.

    Returns:
        String beginning and ending with ``---``, suitable for prepending to
        a Markdown document.
    """
    data: dict[str, object] = {
        "type": "reference",
        "title": content.title,
        "url": content.url,
        "source_type": content.source_type,
        "created_at": created_at,
        "tags": [],
        "read_state": "unread",
    }
    for field in ("author", "description", "published"):
        if content.metadata.get(field):
            data[field] = content.metadata[field]
    if "low_confidence" in content.metadata:
        data["low_confidence"] = content.metadata["low_confidence"]
    data["generated"] = {"by": generator_actor(), "at": created_at}
    data["sources"] = [_source_entry(content)]
    body = yaml.dump(data, allow_unicode=True, sort_keys=False)
    return f"---\n{body}---\n"


def body_sha16(markdown: str) -> str:
    """Return the D9 freshness hash of the post-frontmatter body.

    The identity is the raw UTF-8 bytes of everything after the closing
    ``---`` of the frontmatter block, exactly as stored — no normalization.
    A frontmatter-only rewrite does not move the hash; a body edit does.

    Args:
        markdown: Raw Markdown text with (or without) YAML frontmatter.

    Returns:
        The first 16 hex characters of the SHA-256 body digest. Bodyless
        frontmatter hashes the empty body deterministically.
    """
    m = FM_BLOCK.match(markdown)
    body = markdown[m.end() :] if m else markdown
    import hashlib

    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def set_summary_fields(markdown: str, summary: str, basis: str) -> str:
    """Set the bot-side ``summary`` + ``summary_basis`` frontmatter keys.

    Args:
        markdown: Raw Markdown text with YAML frontmatter.
        summary: The short bot summary (≤ 500 chars, enforced by callers).
        basis: The body hash (see :func:`body_sha16`).

    Returns:
        Modified Markdown, or the original when no frontmatter is present.
    """
    m = FM_BLOCK.match(markdown)
    if not m:
        return markdown
    try:
        data: dict[str, Any] = yaml.safe_load(m.group(2)) or {}
    except yaml.YAMLError:
        return markdown
    data["summary"] = summary
    data["summary_basis"] = basis
    body = yaml.dump(data, allow_unicode=True, sort_keys=False)
    return f"---\n{body}---\n{markdown[m.end() :]}"
