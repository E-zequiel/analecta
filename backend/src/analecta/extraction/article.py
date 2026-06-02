from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import httpx
import trafilatura
from bs4 import BeautifulSoup, Tag
from readability import Document

from analecta.extraction.core import ExtractedContent, ExtractionError, SourceExtractor

if TYPE_CHECKING:
    from analecta.extraction.tier2 import Tier2Result

_HEADERS = {"User-Agent": "analecta/0.1.0 (+https://github.com/E-zequiel/analecta)"}
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
# readability-lxml removes <ul>/<ol> when weight<25 AND link_density>0.2.
# Rescue threshold matches that rule so no content list slips through.
_LIST_RESCUE_DENSITY = 0.2

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
        if link_chars / len(total) <= _LIST_RESCUE_DENSITY:
            continue
        for li in ul.find_all("li", recursive=False):
            p = soup.new_tag("p")
            for child in list(li.contents):
                p.append(child.extract())
            ul.insert_before(p)
        ul.decompose()
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


_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


def _strip_heading_classes(html: str) -> str:
    """Remove class attributes and noise wrappers from heading elements.

    Three readability failure modes are addressed:

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
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_HEADING_TAGS):
        tag.attrs.pop("class", None)
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
    produces garbage.  App Router sites are handled by readability/trafilatura
    (Tier 1) or by Defuddle via the Chromium render path (Tier 2).

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
    """Return True when Tier 1 extraction likely missed JS-rendered content."""
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


def _build_from_defuddle(url: str, t: Tier2Result) -> ExtractedContent:
    """Construct an ``ExtractedContent`` from a successful Defuddle Tier 2 result."""
    metadata: dict[str, Any] = {"extractor": "defuddle"}
    if t.author:
        metadata["author"] = t.author
    if t.description:
        metadata["description"] = t.description
    if t.published:
        metadata["published"] = t.published
    return ExtractedContent(
        title=t.title or "",
        html=t.content or "",
        url=url,
        source_type="article",
        metadata=metadata,
    )


class ArticleExtractor(SourceExtractor):
    """Extracts web article content using a two-tier pipeline.

    Tier 1 (fast, no browser):
        1. Fetch HTML via ``httpx``.
        2. Try Next.js Pages Router hydration data (``__NEXT_DATA__`` JSON blob).
        3. Try ``readability-lxml`` and ``trafilatura``; prefer readability unless
           trafilatura yields > 1.5x more content.

    Tier 2 (Chromium render, on low-confidence Tier 1 result):
        4. POST to the Electron render server; Defuddle runs inside the live DOM
           with ``getComputedStyle()`` available (same quality as Obsidian Web Clipper).
        5. If Defuddle fails, fall back to the returned ``outerHtml`` through Tier 1.
    """

    async def extract(self, url: str) -> ExtractedContent:
        """Fetch and extract article content from *url*.

        Args:
            url: Article URL.

        Returns:
            Populated ``ExtractedContent`` with ``source_type="article"``.

        Raises:
            ExtractionError: If no extraction strategy succeeds.
            httpx.HTTPStatusError: If the server returns a non-2xx response.
        """
        html = await self._fetch(url)
        result = self._parse(html, url)

        if _is_low_confidence(html, result.html):
            try:
                from analecta.extraction.tier2 import render_url

                tier2 = await render_url(url)
                if tier2.ok and tier2.content:
                    return _build_from_defuddle(url, tier2)
                if tier2.outer_html:
                    return self._parse(tier2.outer_html, url)
            except Exception:
                pass

        return result

    async def _fetch(self, url: str) -> str:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_HEADERS)
            response.raise_for_status()
            return response.text

    def _parse(self, html: str, url: str) -> ExtractedContent:
        meta = trafilatura.extract_metadata(html, default_url=url)
        clean = _strip_hidden_elements(html)
        clean = _simplify_figure_images(clean)
        clean = _unwrap_sections(clean)
        clean = _strip_heading_classes(clean)
        clean = _rescue_linked_lists(clean)

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
        metadata = {"extractor": extractor}
        _populate_metadata(metadata, meta)
        return ExtractedContent(
            title=title,
            html=content,
            url=url,
            source_type="article",
            metadata=metadata,
        )
