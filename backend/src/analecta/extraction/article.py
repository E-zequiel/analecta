import httpx
import trafilatura
import trafilatura.settings
from readability import Document

from analecta.extraction.core import ExtractedContent, ExtractionError, SourceExtractor

_HEADERS = {"User-Agent": "analecta/0.1.0 (+https://github.com/E-zequiel/analecta)"}
_TIMEOUT = 30.0
_MIN_CONTENT_LEN = 100


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
        content = trafilatura.extract(
            html,
            url=url,
            output_format="html",
            include_tables=True,
            include_images=True,
            fast=True,
        )
        if content:
            meta = trafilatura.extract_metadata(html, default_url=url)
            title = (meta.title or "") if meta is not None else ""
            return ExtractedContent(
                title=title,
                html=content,
                url=url,
                source_type="article",
                metadata={"extractor": "trafilatura"},
            )

        # Fallback: readability-lxml
        doc = Document(html)
        content = doc.summary()
        if not content or len(content) < _MIN_CONTENT_LEN:
            raise ExtractionError(f"Could not extract content from {url}")

        return ExtractedContent(
            title=doc.title(),
            html=content,
            url=url,
            source_type="article",
            metadata={"extractor": "readability"},
        )
