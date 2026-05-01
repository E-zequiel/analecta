from analecta.extraction.core import ExtractedContent, SourceExtractor


class SubstackExtractor(SourceExtractor):
    """Extracts Substack posts via trafilatura (server-side rendered HTML).

    Substack renders full content server-side, so the standard article
    extraction pipeline handles it without special treatment.
    """

    async def extract(self, url: str) -> ExtractedContent:
        """Extract a Substack post, delegating to ``ArticleExtractor``.

        Args:
            url: Substack post URL (``*.substack.com`` or custom domain).

        Returns:
            ``ExtractedContent`` with ``source_type="substack"``.

        Raises:
            ExtractionError: If article extraction fails.
        """
        from analecta.extraction.article import ArticleExtractor

        result = await ArticleExtractor().extract(url)
        return ExtractedContent(
            title=result.title,
            html=result.html,
            url=url,
            source_type="substack",
            metadata={**result.metadata, "platform": "substack"},
        )


class XExtractor(SourceExtractor):
    """X/Twitter extraction stub — not implemented.

    Nitter is defunct. The X API requires authentication.
    Implementing this extractor is deferred until a viable public path exists.
    """

    async def extract(self, url: str) -> ExtractedContent:
        """Raise ``NotImplementedError`` for any X/Twitter URL.

        Args:
            url: X/Twitter status URL.

        Raises:
            NotImplementedError: Always. X extraction has no viable path.
        """
        raise NotImplementedError(
            "X/Twitter extraction is not supported: Nitter is defunct and the X API "
            f"requires authentication. URL recorded but not extracted: {url}"
        )
