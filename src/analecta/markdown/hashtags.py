"""Hashtag utilities — M4 pipeline."""

import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_HEADING_HASHTAG_RE = re.compile(r"^##[^\s#].*", re.MULTILINE)


def normalize_tag(tag: str) -> str:
    """Normalize *tag* to ``snake_case``.

    Steps: NFKD → ASCII, lowercase, non-alphanumeric → underscore, collapse
    repeated underscores, strip leading/trailing underscores.

    Args:
        tag: Raw tag string (may contain spaces, unicode, special chars).

    Returns:
        Normalized snake_case tag, or ``''`` if nothing remains after
        normalization.
    """
    normalized = unicodedata.normalize("NFKD", tag)
    ascii_str = normalized.encode("ascii", "ignore").decode()
    lowered = ascii_str.lower()
    underscored = _NON_ALNUM_RE.sub("_", lowered)
    collapsed = _MULTI_UNDERSCORE_RE.sub("_", underscored)
    return collapsed.strip("_")


def append_tags(markdown: str, tags: list[str]) -> str:
    """Append normalized hashtags as a line suffix at the end of *markdown*.

    Tags appear on their own line at the end of the document (never at the
    beginning of a line where they could be mistaken for headings).

    Args:
        markdown: Existing Markdown content.
        tags: Raw tag strings to normalize and append.

    Returns:
        *markdown* unchanged if *tags* is empty or all tags normalize to
        ``''``; otherwise *markdown* + a blank line + ``#tag1 #tag2 ...``.
    """
    if not tags:
        return markdown
    normalized = [normalize_tag(t) for t in tags]
    valid = [t for t in normalized if t]
    if not valid:
        return markdown
    tag_line = " ".join(f"#{t}" for t in valid)
    return markdown.rstrip("\n") + "\n\n" + tag_line + "\n"


def find_heading_hashtags(markdown: str) -> list[str]:
    """Find ``##word`` patterns (no space) that render as broken headings.

    A line like ``##Python`` is valid CommonMark h2 syntax but is almost
    certainly a mistyped hashtag. This function surfaces such lines so the
    caller can warn or fix them before writing to disk.

    Args:
        markdown: Markdown content to inspect.

    Returns:
        List of matching lines. Empty list if no anti-patterns are found.
    """
    return _HEADING_HASHTAG_RE.findall(markdown)
