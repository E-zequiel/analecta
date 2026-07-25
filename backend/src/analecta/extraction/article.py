from __future__ import annotations

import difflib
import json
import logging
import re
from typing import Any

import httpx2
import trafilatura
from bs4 import BeautifulSoup, Comment, Tag
from readability import Document
from readability.readability import REGEXES as _READABILITY_REGEXES
from readability.readability import clean as _readability_clean_text

from analecta.extraction.core import ExtractedContent, ExtractionError, SourceExtractor
from analecta.extraction.http_identity import build_headers
from analecta.extraction.ssrf import block_redirect_to_internal, validate_fetch_url
from analecta.extraction.tweet_embeds import resolve_embedded_tweets

log = logging.getLogger(__name__)

_TIMEOUT = 30.0
_MIN_CONTENT_LEN = 100

# Matches elements hidden via CSS utility class (e.g. MDN live-sample base styles).
_HIDDEN_CLASS_RE = re.compile(r"\bhidden\b")


def _strip_hidden_elements(html: str) -> str:
    """Remove elements marked hidden via CSS class before extraction.

    Sites such as MDN include base-style code blocks (colors, borders, etc.)
    that are part of live demo infrastructure but hidden from the reader via
    ``class="hidden"``.  Readability nonetheless extracts them; stripping them
    here prevents spurious code blocks in the converted Markdown.
    """
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(class_=_HIDDEN_CLASS_RE):
        el.decompose()
    return str(soup)


def _simplify_figure_images(html: str) -> str:
    """Hoist <img> tags to direct <figure> children and unwrap bare figure wrappers.

    Two readability failure modes are addressed:

    1. **Inner wrappers** (e.g. milkroad.com):
       ``<figure><div><a><img/></a></div></figure>``.
       readability-lxml scores inner ``<div>``/``<a>`` as low-text-density and strips
       them, leaving empty ``<figure>`` shells.  The ``<img>`` is hoisted to a direct
       child of ``<figure>``.

    2. **Outer wrappers** (e.g. Substack):
       ``<div class="captioned-image-container"><figure>…</figure></div>``.
       readability scores the zero-text ``<div>`` at zero and discards the whole subtree
       including the ``<figure>``.  Any ``<div>`` whose sole meaningful content is a
       single ``<figure>`` is unwrapped so the figure becomes a direct sibling of the
       surrounding prose.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Pass 1: hoist <img> inside each figure to a direct child.
    for fig in soup.find_all("figure"):
        for img in fig.find_all("img"):
            img.extract()
            fig.insert(0, img)
        for el in fig.find_all(["div", "a"]):
            if not el.get_text(strip=True) and not el.find("img"):
                el.decompose()

    # Pass 2: unwrap <div> elements that contain only a single <figure>.
    for fig in soup.find_all("figure"):
        parent = fig.parent
        if parent is None or parent.name != "div":
            continue
        meaningful = [
            c
            for c in parent.children
            if getattr(c, "name", None) or (isinstance(c, str) and c.strip())
        ]
        if len(meaningful) == 1:
            parent.unwrap()

    return str(soup)


_NAV_TAGS = frozenset({"nav", "header", "footer", "aside"})
# readability-lxml removes <ul>/<ol>/<table> (among other container tags) when
# weight<25 AND link_density>0.2. Rescue threshold matches that rule so no
# content list or table slips through. Shared by _rescue_linked_lists and
# _rescue_linked_tables below.
_LINK_DENSITY_RESCUE_THRESHOLD = 0.2

# Matches sole-content loading placeholders left by client-side React components.
# Allows "Loading", "Loading...", "Loading affected packages…" (≤3 words after
# "Loading") but NOT full sentences — requires trailing "..." / "…" or bare word.
_LOADING_RE = re.compile(r"^Loading(\s+\w+){0,3}\s*(\.\.\.|…)?$", re.IGNORECASE)


def _rescue_linked_lists(html: str) -> str:
    """Inline high-link-density list items as sibling <p> tags before readability.

    readability-lxml removes ``<ul>``/``<ol>`` when ``weight < 25`` AND
    ``link_density > 0.2`` (its internal "too many links" rule).  The threshold
    here matches that rule exactly so every content list readability would drop
    is rescued instead.  Converting to sibling ``<p>`` elements is the only safe
    approach — readability never targets ``<p>`` individually.

    Lists inside ``<nav>``, ``<header>``, ``<footer>``, or ``<aside>`` are left
    untouched so readability can still prune navigation menus.
    """
    soup = BeautifulSoup(html, "html.parser")
    for ul in soup.find_all(["ul", "ol"]):
        if any(p.name in _NAV_TAGS for p in ul.parents if p.name):
            continue
        total = ul.get_text()
        if not total.strip():
            continue
        link_chars = sum(len(a.get_text()) for a in ul.find_all("a"))
        if link_chars / len(total) <= _LINK_DENSITY_RESCUE_THRESHOLD:
            continue
        for li in ul.find_all("li", recursive=False):
            p = soup.new_tag("p")
            for child in list(li.contents):
                p.append(child.extract())
            ul.insert_before(p)
        ul.decompose()
    return str(soup)


def _readability_class_weight(tag: Tag) -> int:
    """Replicate readability.Document.class_weight for a bs4 Tag's class/id.

    Mirrors only the class/id half of the real ``class_weight`` (not the
    tag-name/custom-keyword terms, which this project's ``Document(clean)``
    call never configures — ``positive_keywords``/``negative_keywords``
    default to ``None``). A bs4 ``Tag``'s ``class`` attribute is a list, not
    the plain string ``class_weight`` expects from an lxml element, so it
    can't be called directly on one; this reimplements the same two regex
    checks (``REGEXES["negativeRe"]``/``REGEXES["positiveRe"]``, imported
    from the installed package so it can't drift from what real readability
    actually matches on this version). Re-verify against
    ``readability.readability.class_weight`` on any readability-lxml bump.
    """
    weight = 0
    for feature in (tag.get("class"), tag.get("id")):
        if not feature:
            continue
        feature_str = " ".join(feature) if isinstance(feature, list) else str(feature)
        if _READABILITY_REGEXES["negativeRe"].search(feature_str):
            weight -= 25
        if _READABILITY_REGEXES["positiveRe"].search(feature_str):
            weight += 25
    return weight


def _rescue_linked_tables(html: str) -> str:
    """Flatten high-link-density <table>s into sibling <p> tags before readability.

    readability-lxml's "too many links" rule (``weight < 25`` AND
    ``link_density > 0.2``) applies to ``<table>`` the same way it applies to
    ``<ul>``/``<ol>`` (see ``_rescue_linked_lists`` above) — a small reference
    table whose cells are almost entirely link text (e.g. an MDN
    "Specifications" table linking to a single spec) triggers it and the
    whole table is silently dropped. Converting each non-header-only row to a
    sibling ``<p>`` (cells joined with a middle-dot separator) preserves the
    actual content, link included, even though the tabular layout doesn't
    survive — readability never targets ``<p>`` individually. Header-only
    rows (every cell a ``<th>``) are dropped rather than turned into a
    redundant label paragraph.

    Unlike ``_rescue_linked_lists``, this checks ``weight`` too (via
    ``_readability_class_weight``), not link density alone: dissolving a
    table's columnar structure into paragraphs is a much bigger loss than
    dissolving a list's bullets, so a table readability would actually have
    *kept* (an explicit positive-weight class/id, giving ``weight >= 25``)
    must not be rescued just because it happens to be link-dense — that
    would flatten a normal content/comparison table for no reason. Doesn't
    account for readability's separate, weight-independent
    "too-short-content" removal rule (see ``_unwrap_code_examples`` for that
    one) — a table hit by both would need its own fix, not yet observed.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        total = table.get_text()
        if not total.strip():
            continue
        if _readability_class_weight(table) >= 25:
            continue
        link_chars = sum(len(a.get_text()) for a in table.find_all("a"))
        if link_chars / len(total) <= _LINK_DENSITY_RESCUE_THRESHOLD:
            continue
        # Hoist past a <figure class="table-container">-style wrapper (if the
        # table is its only tag child) so the rescued paragraphs don't end up
        # trapped inside an otherwise-empty figure element.
        parent = table.parent
        anchor = table
        if parent is not None and parent.name == "figure":
            siblings = [
                c for c in parent.find_all(True, recursive=False) if c is not table
            ]
            if not siblings:
                anchor = parent
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if not cells or all(c.name == "th" for c in cells):
                continue
            p = soup.new_tag("p")
            for i, cell in enumerate(cells):
                if i > 0:
                    p.append(" · ")
                for child in list(cell.contents):
                    p.append(child.extract())
            anchor.insert_before(p)
        anchor.decompose()
    return str(soup)


def _clone_cell(cell: Tag) -> Tag:
    """Deep-clone a ``<td>``/``<th>`` tag, stripping any span attributes."""
    clone = BeautifulSoup(str(cell), "html.parser").find(cell.name)
    assert isinstance(clone, Tag)
    for attr in ("rowspan", "colspan"):
        if clone.has_attr(attr):
            del clone[attr]
    return clone


def _expand_table_spans(html: str) -> str:
    """Expand rowspan/colspan table cells into duplicate plain cells.

    GFM/Markdown tables have no concept of merged cells, and markdownify's
    table converter (confirmed empirically — no rowspan/colspan handling
    anywhere in the installed package) converts each ``<tr>`` using exactly
    the ``<td>``/``<th>`` elements it literally contains. A row whose merged
    cells are inherited from an earlier row via ``rowspan`` therefore has too
    few cells, and its remaining values shift into the wrong column once
    rendered (e.g. MDN's "Complete cascade order" table, which uses
    ``rowspan`` on the first and third columns to avoid repeating them for
    every row in a group).

    Rebuilds each ``<thead>``/``<tbody>``/``<tfoot>`` section (or the bare
    ``<tr>`` children of a sectionless ``<table>``) as an explicit grid,
    cloning a spanning cell's content into every row/column it visually
    covers, then replaces the original cells. Cloned cells lose their span
    attributes — each one now covers exactly one row/column. Lossy in that
    repeated values become explicit rather than visually implied, but that
    matches how a person reading the rendered table would fill in a merged
    cell anyway, and is the standard approach other HTML-to-Markdown tools
    use for this case.

    Assumes a well-formed table where every row spans the same total column
    count (true of realistic tables, including every case seen so far) —
    that count is taken from the first row of each section, which by
    construction can't yet have any pending rowspan from a previous row.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        sections: list[list[Tag]] = []
        for direct in table.find_all(["thead", "tbody", "tfoot"], recursive=False):
            sections.append(direct.find_all("tr", recursive=False))
        if not sections:
            sections = [table.find_all("tr", recursive=False)]

        for rows in sections:
            if not rows:
                continue
            first_cells = rows[0].find_all(["td", "th"], recursive=False)
            total_columns = sum(int(c.get("colspan", 1) or 1) for c in first_cells)
            if total_columns == 0:
                continue

            # col -> [remaining rows this span still covers, source cell to clone]
            pending: dict[int, list[Any]] = {}
            for row in rows:
                original_cells = row.find_all(["td", "th"], recursive=False)
                cell_ptr = 0
                col = 0
                new_children: list[Tag] = []
                while col < total_columns:
                    if col in pending:
                        remaining, source = pending[col]
                        new_children.append(_clone_cell(source))
                        if remaining > 1:
                            pending[col] = [remaining - 1, source]
                        else:
                            del pending[col]
                        col += 1
                        continue
                    if cell_ptr >= len(original_cells):
                        # Malformed row (fewer real cells than the section's
                        # column count) — stop rather than fabricate cells.
                        break
                    cell = original_cells[cell_ptr]
                    cell_ptr += 1
                    rowspan = int(cell.get("rowspan", 1) or 1)
                    colspan = int(cell.get("colspan", 1) or 1)
                    for i in range(colspan):
                        if i == 0:
                            for attr in ("rowspan", "colspan"):
                                if cell.has_attr(attr):
                                    del cell[attr]
                            new_children.append(cell)
                        else:
                            new_children.append(_clone_cell(cell))
                        if rowspan > 1:
                            pending[col + i] = [rowspan - 1, cell]
                    col += colspan
                row.clear()
                for c in new_children:
                    row.append(c)
    return str(soup)


# readability.Document's default min_text_length — the threshold
# _unwrap_code_examples, _rescue_short_nested_lists, and
# _rescue_short_figure_labels below each work around for a different tag
# shape it hits.
_READABILITY_MIN_TEXT_LEN = 25


def _rescue_short_nested_lists(html: str) -> str:
    """Inline a labeled <li>'s short nested list before readability.

    readability-lxml's conditional cleaning walks ``<ul>``/``<ol>`` (among
    other container tags) innermost-first and drops any whose own text
    content is under ``min_text_length`` (25 chars, its default) with no
    ``<img>`` child — the same rule ``_unwrap_code_examples`` below works
    around for a ``<div>`` wrapper, but here it hits the ``<ul>`` itself.
    socket.dev's threat-infrastructure lists are shaped as
    ``<li>Execution telemetry path:<ul><li><code>/api/x</code></li></ul>
    </li>`` — a single short API path in its own nested list, genuine
    content rather than decorative cruft, but well under the 25-char
    threshold sized for the latter. A sibling nested list long enough to
    clear the threshold (e.g. a 4-item "Payload delivery paths:" list on
    the same page) is untouched — only the short one gets folded.

    Dissolves the nested list into the parent ``<li>``'s own text
    (sub-items joined with ", ") rather than a sibling ``<p>`` the way
    ``_rescue_linked_lists`` does — a sibling here would visually detach
    the values from their label. Lists inside ``<nav>``/``<header>``/
    ``<footer>``/``<aside>`` are left alone so readability can still prune
    navigation menus (matches ``_rescue_linked_lists``'s own guard). Also
    skips a nested list with a negative-weight class/id (readability's own
    unrelated-to-length signal to drop it, e.g. ``class="sidebar"``) rather
    than overriding it.
    """
    soup = BeautifulSoup(html, "html.parser")
    for li in soup.find_all("li"):
        nested = li.find(["ul", "ol"], recursive=False)
        if nested is None:
            continue
        if any(p.name in _NAV_TAGS for p in li.parents if p.name):
            continue
        # Whitespace-collapse the same way readability's own text_length()
        # does (readability.readability.clean) before comparing — a bare
        # bs4 get_text() keeps pretty-printed indentation as literal text,
        # which could push a genuinely-short list's raw length past the
        # threshold while readability's own (collapsed) length is still
        # under it.
        text = _readability_clean_text(nested.get_text())
        if not text or len(text) >= _READABILITY_MIN_TEXT_LEN:
            continue
        if nested.find("img") is not None:
            continue
        if _readability_class_weight(nested) < 0:
            continue
        items = nested.find_all("li", recursive=False)
        li.append(" ")
        for i, item in enumerate(items):
            if i > 0:
                li.append(", ")
            for child in list(item.contents):
                li.append(child.extract())
        nested.decompose()
    return str(soup)


def _rescue_short_figure_labels(html: str) -> str:
    """Unwrap a short label <div> immediately preceding a <figure>.

    Another member of the readability ``min_text_length`` family (see
    ``_rescue_short_nested_lists`` above): readability drops any ``<div>``
    (among other conditional-clean tags) under 25 chars with no ``<img>``.
    system76's blog wraps each bold caption ("See-through", "Nearly
    opaque") in its own near-empty
    ``<div class="prose ..."><p><strong>...</strong></p></div>``
    immediately before the ``<figure>`` it labels. The *first* label in a
    section usually survives by accident — grouped in the same div as the
    section's ``<h3>``/intro paragraph, comfortably over the threshold —
    which masks that every subsequent standalone label div on the same
    page is silently dropped.

    Unwrapping the div (replacing it with its own bare ``<p>`` child) is
    enough: ``<p>`` isn't one of readability's conditionally-cleaned tags
    (``table``/``ul``/``div``/``aside``/``header``/``footer``/``section``),
    so once freed of its wrapper the label survives on its own, and
    there's nothing else in these near-empty divs worth keeping wrapped.

    Scoped narrowly so it can't defeat the length rule for genuine
    decorative cruft: only fires when the ``<div>``'s sole child is one
    ``<p>``, and that ``<div>`` is immediately followed by a ``<figure>``
    sibling — the exact label-then-image shape observed, not any short
    div anywhere.
    """
    soup = BeautifulSoup(html, "html.parser")
    for div in soup.find_all("div"):
        children = div.find_all(True, recursive=False)
        if len(children) != 1 or children[0].name != "p":
            continue
        next_el = div.find_next_sibling(True)
        if next_el is None or next_el.name != "figure":
            continue
        text = _readability_clean_text(div.get_text())
        if not text or len(text) >= _READABILITY_MIN_TEXT_LEN:
            continue
        if div.find("img") is not None:
            continue
        if _readability_class_weight(div) < 0:
            continue
        div.unwrap()
    return str(soup)


def _rescue_syntax_footnote(html: str) -> str:
    """Unwrap the ``<footer>`` note that follows MDN's "Formal syntax" ``<pre>``.

    MDN's Formal syntax section renders as
    ``<pre class="css-formal-syntax">...</pre><footer>This syntax reflects
    the latest standard as per ...</footer>`` — a short prose footnote, made
    up mostly of spec-reference links, immediately after the syntax diagram.
    readability-lxml's conditional cleaning applies the same "too many
    links" rule (``weight < 25`` and ``link_density > 0.2``, see
    ``_rescue_linked_lists``/``_rescue_linked_tables``) to ``<footer>`` that
    it does to ``<table>``/``<ul>``/``<div>`` — the footnote has no class/id
    (weight 0) and roughly half its text is spec-title link text, clearing
    the 0.2 threshold, so the whole element is silently dropped even though
    the ``<pre>`` right before it survives untouched.

    Gated the same two ways ``_rescue_linked_tables`` is: only a ``<footer>``
    readability would actually drop (``weight < 25`` and link density above
    threshold) is touched, and only when it's immediately preceded by a
    ``<pre>`` sibling — the exact shape of this MDN footnote. MDN's real
    page-wide site footer (``<footer class="footer">``, far outside the
    article body, never preceded by a ``<pre>``) has a different tag pattern
    entirely and must not be unwrapped into the article as a stray
    paragraph.
    """
    soup = BeautifulSoup(html, "html.parser")
    for footer in soup.find_all("footer"):
        prev = footer.find_previous_sibling(True)
        if prev is None or prev.name != "pre":
            continue
        total = footer.get_text()
        if not total.strip():
            continue
        if _readability_class_weight(footer) >= 25:
            continue
        link_chars = sum(len(a.get_text()) for a in footer.find_all("a"))
        if link_chars / len(total) <= _LINK_DENSITY_RESCUE_THRESHOLD:
            continue
        p = soup.new_tag("p")
        for child in list(footer.contents):
            p.append(child.extract())
        footer.replace_with(p)
    return str(soup)


def _unwrap_code_examples(html: str) -> str:
    """Replace MDN's ``<div class="code-example">`` wrapper with its bare ``<pre>``.

    MDN wraps every code sample as
    ``<div class="code-example"><div class="example-header">…</div><pre>…</pre></div>``.
    readability-lxml's own conditional cleaning drops any ``div`` (among other
    container tags) whose total text content is under ``min_text_length`` (25
    chars) and contains no ``<img>`` — usually decorative cruft, but it also
    nukes a genuinely short one-line snippet (e.g. a single CSS declaration
    illustrating one rule), since the wrapper's whole text — header label plus
    code — is what gets measured. Replacing the wrapper with its bare ``<pre>``
    removes it from that rule entirely: ``pre`` isn't one of the tags
    readability applies conditional cleaning to. The language class
    ``_lang_from_pre`` (``markdown/converter.py``) needs lives on the ``<pre>``
    tag itself, not the discarded wrapper, so nothing is lost.
    """
    soup = BeautifulSoup(html, "html.parser")
    for wrapper in soup.find_all("div", class_="code-example"):
        pre = wrapper.find("pre")
        if pre is None:
            continue
        wrapper.replace_with(pre.extract())
    return str(soup)


def _unwrap_sections(html: str) -> str:
    """Unwrap <section> elements so their children are scored by readability as a unit.

    Sites like Wikipedia (Parsoid HTML) nest ``<section data-mw-section-id>``
    three or more levels deep.  readability-lxml scores each ``<section>`` as an
    independent scoring unit; a deeply-nested section whose paragraphs have high
    link density (common on Wikipedia — nearly every phrase is a wikilink) may
    fall below the content threshold and be dropped entirely, taking its heading
    and body text with it.  Flattening ``<section>`` elements into their parent
    makes all descendant text participate in the parent's content score.

    ``<section>`` carries no extraction-relevant semantics for readability, so
    unwrapping is always safe — confirmed against milkroad.com, socket.dev, and
    Substack (none of which are affected).
    """
    soup = BeautifulSoup(html, "html.parser")
    for sec in soup.find_all("section"):
        sec.unwrap()
    return str(soup)


_INTRO_CONTENT_TAGS = frozenset({"p", "ul", "ol", "table", "dl", "blockquote", "pre"})


def _reunite_intro_with_body(html: str) -> str:
    """Move the <h1>-adjacent intro content into the article body's sibling.

    MDN (and similar reference-doc sites) puts the ``<h1>`` and its intro
    content in one wrapper ``<div>``, with a TOC ``<aside>`` between it
    and the real body content ``<div>`` — three siblings under ``<main>``.
    readability-lxml scores each sibling as an independent candidate and
    keeps only the highest-scoring one's subtree; the intro wrapper scores
    far lower than the body (no headings, little text) and is discarded
    wholesale even though it's genuine content. Reuniting the intro
    content with the body sibling *before* scoring makes them one
    candidate instead of two.

    Moves every direct ``_INTRO_CONTENT_TAGS`` child (``<p>``, ``<ul>``,
    ``<ol>``, ``<table>``, ``<dl>``, ``<blockquote>``, ``<pre>``), not just
    ``<p>`` — an MDN intro is commonly a paragraph followed by a ``<ul>``
    enumerating the topic before a closing paragraph (e.g. the CSS
    Inheritance article's "properties can be categorized in two types"
    list); moving only the ``<p>`` tags orphans the ``<ul>`` in the
    low-scoring wrapper, which readability then drops along with it.

    Deliberately narrow to avoid pulling real chrome (nav, ads, related-post
    widgets) into the article on other sites: only fires when the ``<h1>``'s
    parent has **exactly one** non-chrome (``_NAV_TAGS``) sibling *and* that
    parent has direct content children to move. Only those content tags move
    — never the ``<h1>`` itself (already handled separately via
    ``doc.title()``; moving it would duplicate the title), never the
    ``<aside>``/other siblings.

    Relies on ``_unwrap_sections`` having already run: MDN wraps the intro
    content in a ``<section>`` that must be flattened first, or it won't be
    a direct child of the ``<h1>``'s parent and this is a no-op.
    """
    soup = BeautifulSoup(html, "html.parser")
    landmark = soup.find("main") or soup.find("article")
    scope = landmark if isinstance(landmark, Tag) else soup
    h1 = scope.find("h1")
    if h1 is None:
        return str(soup)
    intro = h1.parent
    if intro is None or intro.name not in {"div", "section"}:
        return str(soup)
    siblings = [s for s in intro.find_next_siblings() if isinstance(s, Tag)]
    body_candidates = [s for s in siblings if s.name not in _NAV_TAGS]
    if len(body_candidates) != 1:
        return str(soup)
    body = body_candidates[0]
    intro_content = intro.find_all(_INTRO_CONTENT_TAGS, recursive=False)
    if not intro_content:
        return str(soup)
    for el in reversed(intro_content):
        body.insert(0, el.extract())
    return str(soup)


_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


def _strip_heading_classes(html: str) -> str:
    """Remove class attributes and noise wrappers from heading elements.

    Four readability failure modes are addressed:

    1. **Class-based penalty** (Substack, GitHub, many platforms): utility
       classes like ``header-anchor-post`` on ``<h2>`` match readability's
       negative-class patterns and cause the heading to be scored low and
       dropped.  All ``class`` attributes are stripped from h1-h6.

    2. **Empty inner elements** (anchor-button UX pattern): ``<div>`` and
       ``<a>`` elements *inside* headings with no text content inflate
       link-density scoring.  They are removed.

    3. **Link-only div wrappers** (Wikipedia Vector 2022 skin and similar):
       sites wrap each heading in a ``<div>`` alongside a sibling element
       that contains only edit/anchor links
       (e.g. ``<span class="mw-editsection">[edit]</span>``).  The link
       inflates the wrapper's link density, causing readability to discard
       the entire ``<div>`` — taking the heading with it.  Any sibling of
       a heading inside a ``<div>`` that has no prose text beyond its link
       text is removed; if the ``<div>`` then contains only the heading, it
       is unwrapped.

    4. **Self-referencing permalink anchor** (MDN and similar): the entire
       heading text is wrapped in a single ``<a href="#{heading-id}">`` used
       to render a hover permalink icon via CSS ``::before`` — there is no
       real link semantic, but readability/trafilatura treat an all-link
       heading as boilerplate and drop it whole. When a heading's only
       meaningful child is an ``<a>`` whose ``href`` points back at the
       heading's own ``id``, the anchor is unwrapped so the bare text
       survives scoring.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_HEADING_TAGS):
        tag.attrs.pop("class", None)
        heading_id = tag.get("id")
        if heading_id:
            meaningful = [
                c
                for c in tag.children
                if not isinstance(c, Comment)
                and (getattr(c, "name", None) or (isinstance(c, str) and c.strip()))
            ]
            if (
                len(meaningful) == 1
                and isinstance(meaningful[0], Tag)
                and meaningful[0].name == "a"
            ):
                anchor = meaningful[0]
                if anchor.get("href") == f"#{heading_id}":
                    anchor.unwrap()
        # Remove empty or link-only children inside the heading itself.
        for child in tag.find_all(["div", "a"]):
            if not child.get_text(strip=True):
                child.decompose()
        # Unwrap parent <div> when siblings add no prose (only link noise).
        parent = tag.parent
        if parent is None or parent.name != "div":
            continue
        for sibling in list(parent.children):
            if getattr(sibling, "name", None) is None or sibling is tag:
                continue
            # Only inline elements (<span>, <a>) can be pure edit-link noise.
            # Never decompose block-level content, other headings, or media.
            if sibling.name not in {"span", "a"}:
                continue
            if sibling.find(["img", "figure", "video", "picture"]):
                continue
            link_text = "".join(a.get_text() for a in sibling.find_all("a"))
            prose = sibling.get_text().replace(link_text, "").strip("[]() \n\t")
            if not prose:
                sibling.decompose()
        remaining = [
            c
            for c in parent.children
            if getattr(c, "name", None) or (isinstance(c, str) and c.strip())
        ]
        if len(remaining) == 1:
            parent.unwrap()
    return str(soup)


def _strip_loading_placeholders(html: str) -> str:
    """Remove client-side 'Loading…' placeholder elements from extracted HTML.

    React/SPA pages render skeleton text such as 'Loading...' or
    'Loading affected packages…' for components that hydrate after page load.
    These strings pass through readability unchanged but add no value to the
    saved Markdown.  Only elements whose *entire* visible text matches the
    pattern are removed; elements with surrounding real content are left alone.
    """
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(["p", "span", "div"]):
        text = el.string
        if text and _LOADING_RE.match(text.strip()):
            el.decompose()
    return str(soup)


_DEK_MIN_LEN = 20
_HERO_ALT_MATCH_RATIO = 0.7
_HERO_SEARCH_MAX_DEPTH = 8
_SKIP_SIBLING_TAGS = frozenset({"style", "script"})


def _find_dek_paragraph(h1: Tag) -> Tag | None:
    """Return the dek/standfirst <p> immediately after *h1*, if any.

    Sites commonly place a one-sentence standfirst directly after the
    ``<h1>``, inside the same header wrapper (e.g. milkroad.com's
    ``newsletterArticleLayoutIntroLine``, socket.dev's Chakra-generated
    ``<p>`` sibling). Only the first non-``<style>``/``<script>`` sibling is
    checked — stray paragraphs further down the header (bylines, share
    widgets) are deliberately not scanned for, to avoid false positives.
    """
    for sib in h1.find_next_siblings():
        if not isinstance(sib, Tag):
            continue
        if sib.name in _SKIP_SIBLING_TAGS:
            continue
        if sib.name == "p" and len(sib.get_text(strip=True)) >= _DEK_MIN_LEN:
            return sib
        return None
    return None


_TITLE_TRUNCATION_SUFFIXES = ("...", "…")
_TRUNCATED_PREFIX_MIN_LEN = 20


def _find_hero_image(h1: Tag, title: str) -> Tag | None:
    """Return an <img> near *h1* whose alt text matches *title*, if any.

    On sites like socket.dev the hero image lives in the same orphaned
    header branch as the dek (see ``_find_dek_paragraph``), several
    ancestor levels above ``<h1>`` — a sibling of the title/dek block, not
    a descendant of it. Walks up from ``<h1>`` looking for the first
    ``<img>`` whose ``alt`` text closely matches the article title, which
    is what distinguishes the actual hero image from other imagery in the
    same branch (e.g. socket.dev's author avatar, which has no matching
    alt text). Sites where the hero image has empty or generic alt text
    are not rescued — that's a missed fix, not a false positive.

    Deliberately compares against *title* (the article's extracted metadata
    title), not ``h1.get_text()``: some sites (Substack) render more than one
    ``<h1>`` on the page, and ``soup.find("h1")`` in ``_rescue_orphaned_header``
    picks the *first* one — the publication name in Substack's header, not
    the article's own headline — which would false-match against a small
    publication-logo ``<img>`` sitting in the same branch.

    A ``title`` that is itself SEO-truncated with a trailing "…" (e.g.
    socket.dev's ``<title>`` tag) can undershoot the similarity ratio even
    when the hero image's ``alt`` holds the full, untruncated headline. For
    that case specifically — and only that case — also accept an ``alt``
    that starts with the truncated title's text (minus the ellipsis). Only
    applied when the de-ellipsized prefix is at least
    ``_TRUNCATED_PREFIX_MIN_LEN`` chars — real SEO truncation cuts a long
    headline down to ~60 chars, so a short prefix (e.g. a truncated "Q&A...")
    is more likely to spuriously prefix-match an unrelated image's alt text.
    """
    if not title:
        return None
    title = title.strip()
    truncated_prefix = None
    for suffix in _TITLE_TRUNCATION_SUFFIXES:
        if title.endswith(suffix):
            prefix = title[: -len(suffix)].strip().lower()
            if len(prefix) >= _TRUNCATED_PREFIX_MIN_LEN:
                truncated_prefix = prefix
            break
    node: Tag | None = h1
    for _ in range(_HERO_SEARCH_MAX_DEPTH):
        node = node.parent
        if node is None:
            return None
        for img in node.find_all("img"):
            alt = str(img.get("alt") or "").strip()
            if not alt:
                continue
            ratio = difflib.SequenceMatcher(None, alt.lower(), title.lower()).ratio()
            if ratio >= _HERO_ALT_MATCH_RATIO:
                return img
            if truncated_prefix and alt.lower().startswith(truncated_prefix):
                return img
        if node.name == "body":
            return None
    return None


def _rescue_orphaned_header(raw_html: str, content: str, title: str) -> str:
    """Prepend a dek paragraph / hero image dropped by readability's scoring.

    readability-lxml and trafilatura each pick a single winning "body"
    candidate. When a site's ``<h1>``, dek, and hero image live in a
    header branch that is structurally separate from the real article body
    (milkroad.com, socket.dev — confirmed by manual DOM inspection), that
    whole branch loses the scoring and never reaches *content*, even though
    it's genuine reader-facing content. Runs against *raw_html* —
    independent of whichever candidate readability/trafilatura picked, the
    same way title/author/description already bypass that scoring via
    trafilatura metadata — and only prepends pieces confirmed missing from
    *content*, so sites where the dek/hero is already part of the winning
    candidate are untouched.
    """
    soup = BeautifulSoup(raw_html, "html.parser")
    h1 = soup.find("h1")
    if h1 is None or not isinstance(h1, Tag):
        return content

    pieces: list[str] = []

    dek = _find_dek_paragraph(h1)
    if dek is not None:
        dek_text = dek.get_text(strip=True)
        content_text = BeautifulSoup(content, "html.parser").get_text()
        if dek_text and dek_text not in content_text:
            clone = BeautifulSoup(str(dek), "html.parser").find("p")
            if isinstance(clone, Tag):
                clone.attrs = {}
                pieces.append(str(clone))

    hero = _find_hero_image(h1, title)
    if hero is not None:
        src = str(hero.get("src") or "")
        if src and src not in content:
            new_img = soup.new_tag("img", src=src)
            alt = hero.get("alt")
            if alt:
                new_img["alt"] = alt
            pieces.append(str(new_img))

    if not pieces:
        return content
    return "".join(pieces) + content


def _collect_text_from_obj(obj: Any, depth: int = 0) -> str:
    """Recursively collect text from a parsed JSON object into an HTML string."""
    if depth > 6:
        return ""
    if isinstance(obj, str) and len(obj) > 50:
        return obj if "<" in obj else f"<p>{obj}</p>"
    if isinstance(obj, dict):
        return " ".join(_collect_text_from_obj(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_collect_text_from_obj(v, depth + 1) for v in obj)
    return ""


def _try_nextjs_hydration(html: str) -> str | None:
    """Extract content from Next.js Pages Router hydration data embedded in the page.

    Handles only the ``__NEXT_DATA__`` JSON blob (Pages Router).  App Router RSC
    payload is intentionally excluded — RSC wire-format strings are protocol
    markers and component references, not readable text, so extracting them
    produces garbage.  App Router sites fall through to readability/trafilatura
    same as any other page; there is no other fallback for one that's still
    too thin after that.

    Returns an HTML string when > 200 words of content is found in
    ``pageProps``, ``None`` otherwise — caller falls through to
    readability/trafilatura.
    """
    soup = BeautifulSoup(html, "html.parser")

    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if isinstance(tag, Tag) and tag.string:
        try:
            data = json.loads(tag.string)
            page_props = data.get("props", {}).get("pageProps", {})
            extracted = _collect_text_from_obj(page_props)
            if len(extracted.split()) >= 200:
                return f"<article>{extracted}</article>"
        except json.JSONDecodeError, TypeError:
            pass

    return None


def _is_low_confidence(raw_html: str, extracted_html: str) -> bool:
    """Return True when extraction likely missed JS-rendered content.

    Surfaced to the reader as a ``low_confidence`` frontmatter key rather
    than acted on — Analecta no longer has a fallback extraction strategy to
    trigger, so this is a diagnostic signal only (see
    ``docs/defuddle-decision.md``).
    """
    text = BeautifulSoup(extracted_html, "html.parser").get_text()
    if len(text.split()) < 200:
        return True
    soup = BeautifulSoup(raw_html, "html.parser")
    all_tags = soup.find_all(True)
    if all_tags and len(soup.find_all("script")) / len(all_tags) > 0.4:
        return True
    return False


def _populate_metadata(metadata: dict[str, Any], meta: Any) -> None:
    """Add author / description / published from a trafilatura metadata object."""
    if not meta:
        return
    if getattr(meta, "author", None):
        metadata["author"] = str(meta.author)
    if getattr(meta, "description", None):
        metadata["description"] = str(meta.description)
    if getattr(meta, "date", None):
        metadata["published"] = str(meta.date)


class ArticleExtractor(SourceExtractor):
    """Extracts web article content via a plain HTTP fetch, no browser rendering.

    1. Fetch HTML via ``httpx2``.
    2. Try Next.js Pages Router hydration data (``__NEXT_DATA__`` JSON blob).
    3. Try ``readability-lxml`` and ``trafilatura``; prefer readability unless
       trafilatura yields > 1.5x more content.

    See ``docs/defuddle-decision.md`` for why there is no browser-rendered
    fallback for pages this misses.
    """

    async def extract(self, url: str) -> ExtractedContent:
        """Fetch and extract article content from *url*.

        Args:
            url: Article URL.

        Returns:
            Populated ``ExtractedContent`` with ``source_type="article"`` and
            a ``low_confidence`` metadata flag (see ``_is_low_confidence``).

        Raises:
            ExtractionError: If no extraction strategy succeeds, or *url*
                targets a blocked scheme/host.
            httpx2.HTTPStatusError: If the server returns a non-2xx response.
        """
        html, final_url = await self._fetch(url)
        html = await resolve_embedded_tweets(html)
        result = self._parse(html, final_url)
        result.metadata["low_confidence"] = _is_low_confidence(html, result.html)
        return result

    async def _fetch(self, url: str) -> tuple[str, str]:
        """Fetch *url*, following redirects.

        Returns:
            Tuple of ``(html, final_url)`` — ``final_url`` is the
            post-redirect URL (``response.url``), used as the base for
            resolving relative asset paths and as the canonical article URL.

        Raises:
            ExtractionError: If *url* (or any redirect hop along the way)
                targets a blocked scheme/host — see ``ssrf.py``.
        """
        validate_fetch_url(url)
        async with httpx2.AsyncClient(
            follow_redirects=True,
            timeout=_TIMEOUT,
            event_hooks={"response": [block_redirect_to_internal]},
        ) as client:
            response = await client.get(url, headers=build_headers("document"))
            response.raise_for_status()
            return response.text, str(response.url)

    def _parse(self, html: str, url: str) -> ExtractedContent:
        meta = trafilatura.extract_metadata(html, default_url=url)
        clean = _strip_hidden_elements(html)
        clean = _simplify_figure_images(clean)
        clean = _unwrap_sections(clean)
        clean = _reunite_intro_with_body(clean)
        clean = _strip_heading_classes(clean)
        clean = _rescue_linked_lists(clean)
        clean = _rescue_linked_tables(clean)
        clean = _expand_table_spans(clean)
        clean = _rescue_short_nested_lists(clean)
        clean = _rescue_short_figure_labels(clean)
        clean = _rescue_syntax_footnote(clean)
        clean = _unwrap_code_examples(clean)

        doc = Document(clean)
        readability_html = doc.summary() or ""
        traf_html = (
            trafilatura.extract(
                clean,
                output_format="html",
                include_comments=False,
                include_tables=True,
                include_images=True,
                favor_precision=True,
            )
            or ""
        )

        # Prefer readability: it preserves <code>/<pre> structure and list
        # semantics correctly. trafilatura is used only when it extracts
        # substantially more content (>1.5x) — a sign it has found depth
        # that readability missed (long technical posts, multi-section articles).
        use_traf = len(traf_html) > len(readability_html) * 1.5
        if use_traf:
            content, extractor = traf_html, "trafilatura"
        else:
            content, extractor = readability_html, "readability"

        content = _strip_loading_placeholders(content)

        # Fall back to Next.js Pages Router hydration only when both DOM
        # extractors come up short — i.e. pure SPAs with no server-rendered
        # HTML body.  SSR sites (Sanity CMS, etc.) have full HTML and should
        # never reach this path: their __NEXT_DATA__ blob contains the full
        # page state including navigation, API docs, and UI strings, which
        # _collect_text_from_obj would pull in indiscriminately.
        if len(content.split()) < 200:
            nextjs_html = _try_nextjs_hydration(html)
            if nextjs_html:
                title = (meta.title if meta else None) or ""
                metadata: dict[str, Any] = {"extractor": "nextjs-hydration"}
                _populate_metadata(metadata, meta)
                return ExtractedContent(
                    title=title,
                    html=nextjs_html,
                    url=url,
                    source_type="article",
                    metadata=metadata,
                )

        if not content or len(content) < _MIN_CONTENT_LEN:
            raise ExtractionError(f"Could not extract content from {url}")

        title = (meta.title if meta else None) or doc.title() or ""
        content = _rescue_orphaned_header(html, content, title)
        metadata = {"extractor": extractor}
        _populate_metadata(metadata, meta)
        return ExtractedContent(
            title=title,
            html=content,
            url=url,
            source_type="article",
            metadata=metadata,
        )
