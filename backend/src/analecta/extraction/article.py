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

        # Try Next.js hydration before readability/trafilatura — handles SPAs that
        # embed the full article in the initial JS payload.
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
