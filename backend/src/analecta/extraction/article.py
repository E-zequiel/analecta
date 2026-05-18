import re

import httpx
import trafilatura
from bs4 import BeautifulSoup
from readability import Document

from analecta.extraction.core import ExtractedContent, ExtractionError, SourceExtractor

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


class ArticleExtractor(SourceExtractor):
    """Extracts web article content using trafilatura with readability-lxml fallback.

    Extraction strategy:
        1. Fetch HTML via ``httpx``.
        2. Try ``trafilatura`` (primary).
        3. Fall back to ``readability-lxml``.
        4. Raise ``ExtractionError`` if both fail.
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
        return self._parse(html, url)

    async def _fetch(self, url: str) -> str:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_HEADERS)
            response.raise_for_status()
            return response.text

    def _parse(self, html: str, url: str) -> ExtractedContent:
        meta = trafilatura.extract_metadata(html, default_url=url)
        clean = _strip_hidden_elements(html)
        doc = Document(clean)

        readability_html = doc.summary() or ""
        traf_html = (
            trafilatura.extract(
                clean, output_format="html", include_comments=False, include_tables=True
            )
            or ""
        )

        # Prefer readability: it preserves <code>/<pre> structure correctly.
        # Fall back to trafilatura when it extracts substantially more content
        # (e.g. short API reference pages that readability prunes too aggressively).
        if len(traf_html) > len(readability_html) * 1.5:
            content, extractor = traf_html, "trafilatura"
        else:
            content, extractor = readability_html, "readability"

        if not content or len(content) < _MIN_CONTENT_LEN:
            raise ExtractionError(f"Could not extract content from {url}")

        title = (meta.title if meta else None) or doc.title() or ""
        return ExtractedContent(
            title=title,
            html=content,
            url=url,
            source_type="article",
            metadata={"extractor": extractor},
        )
