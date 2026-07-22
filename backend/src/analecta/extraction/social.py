import re
from urllib.parse import urljoin, urlparse

import httpx2

from analecta.extraction.core import ExtractedContent, ExtractionError, SourceExtractor
from analecta.extraction.http_identity import build_headers

_INBOX_RE = re.compile(r"^/inbox/post/\d+$")
_TIMEOUT = 8.0


class SubstackExtractor(SourceExtractor):
    """Extracts Substack posts via trafilatura (server-side rendered HTML).

    Substack renders full content server-side, so the standard article
    extraction pipeline handles it without special treatment.

    ``substack.com/inbox/post/<id>`` URLs are resolved to their canonical
    ``*.substack.com/p/<slug>`` form via a HEAD redirect before extraction.
    """

    async def extract(self, url: str) -> ExtractedContent:
        """Extract a Substack post, delegating to ``ArticleExtractor``.

        Inbox URLs (``substack.com/inbox/post/<id>``) are first resolved to
        their canonical ``*.substack.com/p/<slug>`` form via a HEAD request.

        Args:
            url: Substack post URL — ``*.substack.com``, ``substack.com/p/...``,
                or ``substack.com/inbox/post/<id>``.

        Returns:
            ``ExtractedContent`` with ``source_type="substack"`` and ``url``
            set to the fully-resolved post-fetch URL (``ArticleExtractor``'s
            own redirect resolution runs after inbox-URL resolution, so this
            is never staler than the canonical form).

        Raises:
            ExtractionError: If inbox URL cannot be resolved, or article
                extraction fails.
        """
        from analecta.extraction.article import ArticleExtractor

        canonical = await self._resolve_canonical(url)
        result = await ArticleExtractor().extract(canonical)
        return ExtractedContent(
            title=result.title,
            html=result.html,
            url=result.url,
            source_type="substack",
            metadata={**result.metadata, "platform": "substack"},
        )

    async def _resolve_canonical(self, url: str) -> str:
        """Resolve an inbox URL to its canonical form.

        If *url* is not a ``/inbox/post/<id>`` URL, returns it unchanged.

        Args:
            url: Potentially an inbox URL.

        Returns:
            Canonical Substack post URL.

        Raises:
            ExtractionError: If the inbox URL cannot be resolved via redirect.
        """
        if not _INBOX_RE.match(urlparse(url).path):
            return url

        try:
            async with httpx2.AsyncClient(
                follow_redirects=False, timeout=_TIMEOUT
            ) as client:
                resp = await client.head(url, headers=build_headers("document"))
        except Exception as exc:
            raise ExtractionError(
                f"Could not resolve Substack inbox URL {url}: {exc}"
            ) from exc

        if resp.status_code in {301, 302, 303, 307, 308}:
            location = resp.headers.get("location")
            if location:
                return urljoin(url, location)

        raise ExtractionError(
            f"Substack inbox URL did not redirect to a canonical post URL: {url}"
        )
