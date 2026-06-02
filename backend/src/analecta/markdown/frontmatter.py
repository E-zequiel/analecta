"""YAML frontmatter builder — M4 pipeline."""

import yaml

from analecta.extraction.core import ExtractedContent


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


def build_template_block(source_type: str) -> str:
    """Build a Logseq page template block for *source_type*.

    The returned block uses Logseq's ``template::`` property so it can be
    inserted via the template picker in Logseq/Obsidian.

    Args:
        source_type: One of ``"article"``, ``"youtube"``, ``"substack"``,
            ``"x"``.

    Returns:
        Markdown string with the template block.
    """
    return (
        f"- template:: analecta_{source_type}\n"
        "  template-including-parent:: false\n"
        "  - ## Summary\n"
        "  - ## Notes\n"
    )
