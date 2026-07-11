"""YAML frontmatter builder — M4 pipeline."""

import re
from typing import Any

import yaml

from analecta.extraction.core import ExtractedContent

_FM_BLOCK = re.compile(r"^(---\n)([\s\S]*?)(\n---\n)", re.MULTILINE)


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
    m = _FM_BLOCK.match(markdown)
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
        "title": content.title,
        "url": content.url,
        "source_type": content.source_type,
        "created_at": created_at,
        "tags": [],
        "status": "unread",
    }
    for field in ("author", "description", "published"):
        if content.metadata.get(field):
            data[field] = content.metadata[field]
    body = yaml.dump(data, allow_unicode=True, sort_keys=False)
    return f"---\n{body}---\n"
