"""Backlink reference extraction from Markdown.

Parses ``[[wikilinks]]``, inline ``#hashtag`` references, and the ``linked:``
YAML frontmatter field, capturing the nearest preceding heading and a
±60-character context snippet per occurrence.
"""

import re
from dataclasses import dataclass
from typing import Any

_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")
_HASHTAG_RE = re.compile(r"(?<!\S)#([A-Za-z][A-Za-z0-9_]*)(?![A-Za-z0-9_])")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)")
_FENCE_RE = re.compile(r"^```")
_FRONTMATTER_RE = re.compile(r"^---\n[\s\S]*?\n---\n", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

_CONTEXT_RADIUS = 60


@dataclass
class ParsedRef:
    """A single wikilink or hashtag reference found in Markdown.

    Attributes:
        target_text: Lowercased wikilink title or normalized hashtag name.
        is_hashtag: ``True`` when the reference is a ``#hashtag``.
        heading: Nearest preceding heading text, or ``None``.
        pre: Up to 60 chars immediately before the match (stripped).
        highlight: The matched text as it appears in source (e.g. ``[[Title]]``).
        post: Up to 60 chars immediately after the match (stripped).
    """

    target_text: str
    is_hashtag: bool
    heading: str | None
    pre: str
    highlight: str
    post: str


def _mask_inline_code(line: str) -> str:
    """Blank out inline code spans in *line*, preserving length and offsets.

    Prevents ``[[wikilinks]]`` and ``#hashtags`` written inside inline code
    (e.g. `` `[[Not A Link]]` ``) from being mistaken for real references,
    without disturbing character positions used for context extraction.

    Args:
        line: A single line of Markdown (no embedded newlines).

    Returns:
        *line* with each `` `...` `` span replaced by spaces of the same
        length.
    """
    return _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)


def parse_refs(markdown: str) -> list[ParsedRef]:
    """Extract all wikilink, hashtag, and frontmatter-linked references from *markdown*.

    Reads the ``linked:`` list from YAML frontmatter (if present), then
    skips frontmatter and parses ``[[wikilinks]]`` and ``#hashtags`` from
    the body. Skips code-fence blocks, inline code spans, and heading lines.

    Args:
        markdown: Raw Markdown text to parse.

    Returns:
        List of :class:`ParsedRef` objects. Frontmatter-linked entries come
        first, followed by body references in document order.
    """
    import yaml

    from analecta.markdown.hashtags import normalize_tag

    # Collect refs declared in frontmatter ``linked: [...]``.
    fm_refs: list[ParsedRef] = []
    fm_match = re.match(r"^---\n([\s\S]*?)\n---\n", markdown)
    if fm_match:
        try:
            fm_data: dict[str, Any] = yaml.safe_load(fm_match.group(1)) or {}
            for title in fm_data.get("linked") or []:
                fm_refs.append(
                    ParsedRef(
                        target_text=str(title).lower(),
                        is_hashtag=False,
                        heading=None,
                        pre="",
                        highlight=f"[[{title}]]",
                        post="",
                    )
                )
        except Exception:
            pass

    # Strip YAML frontmatter
    body = _FRONTMATTER_RE.sub("", markdown, count=1)

    refs: list[ParsedRef] = []
    current_heading: str | None = None
    in_fence = False

    for line in body.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            current_heading = heading_match.group(1).strip()
            continue

        masked_line = _mask_inline_code(line)

        # Wikilinks: [[Title]] or [[Title|Alias]]
        for m in _WIKILINK_RE.finditer(masked_line):
            start, end = m.start(), m.end()
            target = m.group(1).strip()
            refs.append(
                ParsedRef(
                    target_text=target.lower(),
                    is_hashtag=False,
                    heading=current_heading,
                    pre=line[max(0, start - _CONTEXT_RADIUS) : start].strip(),
                    highlight=m.group(0),
                    post=line[end : end + _CONTEXT_RADIUS].strip(),
                )
            )

        # Hashtags: #word (inline, not heading-style)
        for m in _HASHTAG_RE.finditer(masked_line):
            start, end = m.start(), m.end()
            tag_name = m.group(1)
            refs.append(
                ParsedRef(
                    target_text=normalize_tag(tag_name),
                    is_hashtag=True,
                    heading=current_heading,
                    pre=line[max(0, start - _CONTEXT_RADIUS) : start].strip(),
                    highlight=f"#{tag_name}",
                    post=line[end : end + _CONTEXT_RADIUS].strip(),
                )
            )

    return fm_refs + refs
