"""Backlink reference extraction from Markdown.

Parses ``[[wikilinks]]``, inline ``#hashtag`` references, and the ``linked:``
YAML frontmatter field, capturing the enclosing heading (a ref inside a
heading line belongs to that same heading) and a ±60-character context
snippet per occurrence.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from analecta.markdown.hashtags import normalize_tag

_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")
_HASHTAG_RE = re.compile(r"(?<!\S)#([A-Za-z][A-Za-z0-9_]*)(?![A-Za-z0-9_])")
_HASHTAG_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
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
    the body. Skips code-fence blocks and inline code spans. A heading
    line's ``#`` marker is excluded, but the heading's own text is parsed
    like any other line.

    Args:
        markdown: Raw Markdown text to parse.

    Returns:
        List of :class:`ParsedRef` objects. Frontmatter-linked entries come
        first, followed by body references in document order.
    """
    import yaml

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
            # Exclude only the marker; parse the heading's own text below,
            # tagged with the heading it just opened (self-reference).
            line = heading_match.group(1)

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

        # Hashtags: #word (the heading's own marker was already excluded above)
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


def _rewrite_hashtag_body(
    markdown: str,
    target_normalized: str,
    replace: Callable[[str], str],
) -> tuple[str, int]:
    """Shared line-traversal for rewriting every live ``#hashtag`` occurrence.

    Walks *markdown* exactly like :func:`parse_refs` — respecting
    frontmatter, fenced code blocks, heading markers, and inline code — and
    replaces each occurrence matching *target_normalized* with
    ``replace(original_span)``, where *original_span* is the matched text
    as typed (e.g. ``"#Python"``). Backs both
    :func:`neutralize_hashtag_occurrences` (delete) and
    :func:`rename_hashtag_occurrences` (rename) so "what counts as a live
    occurrence" can never drift between the two mechanisms, or from what
    :func:`parse_refs` itself would index.

    Args:
        markdown: Raw Markdown text to rewrite.
        target_normalized: Normalized (:func:`normalize_tag`) hashtag
            identity to match.
        replace: Called with each matched span's original text; its return
            value replaces that span.

    Returns:
        Tuple of ``(rewritten_markdown, occurrences_changed)``. Returns
        *markdown* unchanged with ``0`` if no live occurrence matches.
    """
    # .match (not .sub) anchors at position 0, so a mid-document ``---``
    # thematic-break block is never mistaken for a frontmatter prefix — the
    # sub-based approach `parse_refs` uses is safe there because it only
    # ever *drops* the matched span, but here the span is reused as a
    # prefix for reconstruction, so it must actually be one.
    fm_match = _FRONTMATTER_RE.match(markdown)
    frontmatter = fm_match.group(0) if fm_match else ""
    stripped_body = markdown[len(frontmatter) :]

    in_fence = False
    count = 0
    out_lines: list[str] = []

    for raw_line in stripped_body.splitlines(keepends=True):
        if raw_line.endswith("\r\n"):
            eol, line = "\r\n", raw_line[:-2]
        elif raw_line.endswith("\n"):
            eol, line = "\n", raw_line[:-1]
        else:
            eol, line = "", raw_line

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(raw_line)
            continue
        if in_fence:
            out_lines.append(raw_line)
            continue

        marker = ""
        content = line
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            marker = line[: heading_match.start(1)]
            content = heading_match.group(1)

        masked = _mask_inline_code(content)
        matches = [
            m
            for m in _HASHTAG_RE.finditer(masked)
            if normalize_tag(m.group(1)) == target_normalized
        ]
        for m in reversed(matches):
            start, end = m.start(), m.end()
            content = f"{content[:start]}{replace(content[start:end])}{content[end:]}"
        count += len(matches)

        out_lines.append(f"{marker}{content}{eol}")

    return frontmatter + "".join(out_lines), count


def neutralize_hashtag_occurrences(
    markdown: str, target_normalized: str
) -> tuple[str, int]:
    """Wrap every live ``#hashtag`` matching *target_normalized* in backticks.

    Used when a tag is deleted: backticking demotes the literal body text to
    inline code, which :func:`parse_refs` already skips (via
    :func:`_mask_inline_code`), so the tag cannot resurrect itself on the
    next :meth:`~analecta.storage.index.VaultIndex.index_backlinks` call.
    Includes heading-embedded hashtags too — a heading-embedded hashtag
    (see ``test_hashtag_in_heading_text_resolves``) is just as live as any
    other and must not become a resurrection loophole.

    Args:
        markdown: Raw Markdown text to rewrite.
        target_normalized: Normalized (:func:`normalize_tag`) hashtag
            identity to neutralize.

    Returns:
        Tuple of ``(rewritten_markdown, occurrences_wrapped)``. Returns
        *markdown* unchanged with ``0`` if no live occurrence matches.
    """
    return _rewrite_hashtag_body(markdown, target_normalized, lambda span: f"`{span}`")


def rename_hashtag_occurrences(
    markdown: str, target_normalized: str, new_name: str
) -> tuple[str, int]:
    """Replace every live ``#hashtag`` matching *target_normalized* with ``#new_name``.

    Used when a tag is renamed: unlike delete's neutralize-via-backtick,
    rename must preserve the body text's continuity with the tag identity
    rather than sever it, so the literal occurrence is migrated to the new
    name instead of being demoted to inline code. Callers must first check
    :func:`is_valid_hashtag_literal` on *new_name* — this function does not
    validate it, and a *new_name* containing symbols or spaces would produce
    a span that no longer parses as a hashtag on the next
    :func:`parse_refs` pass.

    Args:
        markdown: Raw Markdown text to rewrite.
        target_normalized: Normalized (:func:`normalize_tag`) hashtag
            identity to migrate.
        new_name: Literal replacement text (without the leading ``#``).

    Returns:
        Tuple of ``(rewritten_markdown, occurrences_changed)``. Returns
        *markdown* unchanged with ``0`` if no live occurrence matches.
    """
    return _rewrite_hashtag_body(
        markdown, target_normalized, lambda _span: f"#{new_name}"
    )


def is_valid_hashtag_literal(name: str) -> bool:
    """Return whether *name* could appear verbatim as ``#name`` and parse as live.

    Mirrors :data:`_HASHTAG_RE`'s capture-group charset exactly (a leading
    letter, then letters/digits/underscore), so a ``True`` result guarantees
    :func:`rename_hashtag_occurrences`'s output round-trips through
    :func:`parse_refs`. Used to gate whether :meth:`VaultIndex.rename_tag`
    can migrate literal body-text occurrences to *name*, or must reject the
    rename instead.

    Args:
        name: Candidate tag name — typically ``rename_tag``'s *new_name*.

    Returns:
        ``True`` if *name* is a valid bare hashtag token.
    """
    return bool(_HASHTAG_NAME_RE.fullmatch(name))
