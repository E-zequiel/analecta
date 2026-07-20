from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx2
import trafilatura
from bs4 import BeautifulSoup, Comment, Tag
from readability import Document

from analecta.extraction.core import ExtractedContent, ExtractionError, SourceExtractor
from analecta.extraction.http_identity import build_headers

if TYPE_CHECKING:
    from analecta.extraction.tier2 import Tier2Result

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


def _reunite_intro_with_body(html: str) -> str:
    """Move the <h1>-adjacent intro <p> tags into the article body's sibling.

    MDN (and similar reference-doc sites) puts the ``<h1>`` and its intro
    paragraph(s) in one wrapper ``<div>``, with a TOC ``<aside>`` between it
    and the real body content ``<div>`` — three siblings under ``<main>``.
    readability-lxml scores each sibling as an independent candidate and
    keeps only the highest-scoring one's subtree; the intro wrapper scores
    far lower than the body (no headings, little text) and is discarded
    wholesale even though it's genuine content. Reuniting the intro
    paragraphs with the body sibling *before* scoring makes them one
    candidate instead of two.

    Deliberately narrow to avoid pulling real chrome (nav, ads, related-post
    widgets) into the article on other sites: only fires when the ``<h1>``'s
    parent has **exactly one** non-chrome (``_NAV_TAGS``) sibling *and* that
    parent has direct ``<p>`` children to move. Only those ``<p>`` tags move
    — never the ``<h1>`` itself (already handled separately via
    ``doc.title()``; moving it would duplicate the title), never the
    ``<aside>``/other siblings.

    Relies on ``_unwrap_sections`` having already run: MDN wraps the intro
    ``<p>`` tags in a ``<section>`` that must be flattened first, or they
    won't be direct children of the ``<h1>``'s parent and this is a no-op.
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
    intro_paragraphs = intro.find_all("p", recursive=False)
    if not intro_paragraphs:
        return str(soup)
    for p in reversed(intro_paragraphs):
        body.insert(0, p.extract())
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


def _has_live_sample_placeholders(raw_html: str) -> bool:
    """Return True when *raw_html* has MDN-style live-code-sample placeholders.

    MDN's client JS tears down the raw ``<iframe class="sample-code-frame"
    data-live-id="...">`` placeholder shortly after load and replaces it with
    a JS-only custom element (``<mdn-live-sample-result>``) whose real content
    Tier 1 can never see, regardless of how much other text is on the page —
    so this fires Tier 2's embed-capture pass independently of
    ``_is_low_confidence``.
    """
    soup = BeautifulSoup(raw_html, "html.parser")
    return bool(soup.select("iframe.sample-code-frame[data-live-id]"))


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


def _resolve_tier2_url(tier2_final_url: str | None, fallback: str) -> str:
    """Return the browser-reported URL, or *fallback* if it isn't usable.

    ``tier2_final_url`` (``document.baseURI`` from the render server) is only
    trustworthy when navigation actually reached a real page — a failed or
    timed-out navigation can leave ``about:blank`` or a ``chrome-error://``
    value there, which would be a worse base than the httpx-resolved
    ``fallback``.
    """
    if tier2_final_url and tier2_final_url.startswith(("http://", "https://")):
        return tier2_final_url
    return fallback


def _decode_shots(shots: dict[str, str]) -> dict[str, bytes]:
    """Decode a ``Tier2Result.shots`` base64 map to raw PNG bytes, keyed by id.

    Entries that fail to decode (malformed base64) are dropped rather than
    raising — a lost screenshot degrades gracefully to a broken placeholder
    image, matching ``AssetDownloader``'s existing silently-skip convention.
    """
    decoded: dict[str, bytes] = {}
    for shot_id, data in shots.items():
        try:
            decoded[shot_id] = base64.b64decode(data)
        except ValueError, binascii.Error:
            continue
    return decoded


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
        captured_images=_decode_shots(t.shots) if t.shots else {},
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
            httpx2.HTTPStatusError: If the server returns a non-2xx response.
        """
        html, final_url = await self._fetch(url)
        result = self._parse(html, final_url)

        if _is_low_confidence(html, result.html) or _has_live_sample_placeholders(html):
            try:
                from analecta.extraction.tier2 import render_url

                # url not final_url: Chromium follows its own redirects.
                tier2 = await render_url(url)
                resolved_url = _resolve_tier2_url(tier2.final_url, final_url)
                if tier2.ok and tier2.content:
                    if tier2.shots:
                        placeholder_count = tier2.content.count("analecta-shot.invalid")
                        log.info(
                            "Tier 2 defuddle content for %s: %d shot(s) captured, "
                            "%d placeholder(s) present in content",
                            url,
                            len(tier2.shots),
                            placeholder_count,
                        )
                    return _build_from_defuddle(resolved_url, tier2)
                if tier2.outer_html:
                    parsed = self._parse(tier2.outer_html, resolved_url)
                    if tier2.shots:
                        parsed.captured_images = _decode_shots(tier2.shots)
                    return parsed
            except Exception as exc:
                log.warning("Tier 2 render failed for %s: %r", url, exc)

        return result

    async def _fetch(self, url: str) -> tuple[str, str]:
        """Fetch *url*, following redirects.

        Returns:
            Tuple of ``(html, final_url)`` — ``final_url`` is the
            post-redirect URL (``response.url``), used as the base for
            resolving relative asset paths and as the canonical article URL.
        """
        async with httpx2.AsyncClient(
            follow_redirects=True, timeout=_TIMEOUT
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
