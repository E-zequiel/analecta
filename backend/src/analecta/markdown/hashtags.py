"""Hashtag utilities — M4 pipeline."""

import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_HEADING_HASHTAG_RE = re.compile(r"^##[^\s#].*", re.MULTILINE)
_WHITESPACE_RUN_RE = re.compile(r"\s+")


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


def title_to_hashtag_key(title: str) -> str:
    """Fold *title* into the identity space a same-spelled hashtag would use.

    Unlike :func:`normalize_tag`, this does not strip accents or fold
    symbols to underscore — a hashtag's own identity (``backlink_refs.
    target_text``) is ``casefold()``-only and accent/symbol-*preserving*
    (see the "Normalization" section in ``docs/wikilinks-and-hashtags.md``),
    so the title side of a hashtag-to-title match must use the same rule or
    the two can never agree on titles containing an accent or one of the
    hashtag charset's symbols (``- ' ~ ^``). The one unavoidable exception
    is whitespace: no valid hashtag can contain a space, so a multi-word
    title can only ever be referenced by a hashtag that substitutes
    underscores for spaces — this collapses whitespace runs to a single
    underscore before casefolding, mirroring how the hashtag charset
    already treats underscore as an ordinary continuation character.

    Used by :meth:`~analecta.storage.index.VaultIndex.get_backlinks`,
    :meth:`~analecta.storage.index.VaultIndex.get_outgoing_links`,
    :meth:`~analecta.storage.index.VaultIndex.get_subgraph`, and
    :meth:`~analecta.storage.index.VaultIndex.get_graph` to resolve a
    ``#hashtag`` against an entry title.

    Args:
        title: Entry title to fold.

    Returns:
        *title* with leading/trailing whitespace stripped, internal
        whitespace runs replaced by a single underscore, then casefolded.
        Every other character (accents, hyphens, apostrophes, tildes,
        carets) is preserved literally.
    """
    return _WHITESPACE_RUN_RE.sub("_", title.strip()).casefold()


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
