from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

SourceType = Literal["article", "youtube", "substack", "x"]


@dataclass
class ExtractedContent:
    """Output contract for all source extractors (consumed by M3 and M4).

    Attributes:
        title: Article or video title.
        html: Cleaned article HTML or transcript HTML.
        url: Canonical source URL.
        source_type: Detected content category.
        metadata: Extractor-specific key/value pairs (author, video_id, etc.).
    """

    title: str
    html: str
    url: str
    source_type: SourceType
    metadata: dict[str, Any] = field(default_factory=dict)


class ExtractionError(Exception):
    """Raised when a URL cannot be extracted by any available strategy."""


class SourceExtractor(ABC):
    """Abstract base for all source-specific extractors."""

    @abstractmethod
    async def extract(self, url: str) -> ExtractedContent:
        """Extract content from *url* and return a normalised ``ExtractedContent``.

        Args:
            url: The source URL to extract.

        Returns:
            Populated ``ExtractedContent`` instance.

        Raises:
            ExtractionError: If extraction fails after all fallbacks are exhausted.
            NotImplementedError: If the source type has no extraction path.
        """


def detect_source_type(url: str) -> SourceType:
    """Infer the content category from a URL without making network requests.

    Args:
        url: Fully qualified URL.

    Returns:
        One of ``"youtube"``, ``"x"``, ``"substack"``, or ``"article"``.
    """
    host = urlparse(url).netloc.lower()

    if host in {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}:
        return "youtube"

    if host in {
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
        "x.com",
        "www.x.com",
        "mobile.x.com",
    }:
        return "x"

    if host.endswith(".substack.com") or host in {"substack.com", "www.substack.com"}:
        return "substack"

    return "article"


async def extract(url: str) -> ExtractedContent:
    """Detect source type and dispatch to the appropriate extractor.

    Args:
        url: The source URL to extract.

    Returns:
        Populated ``ExtractedContent`` instance.

    Raises:
        ExtractionError: If extraction fails.
        NotImplementedError: If the detected source type has no extraction path.
    """
    from analecta.extraction.article import ArticleExtractor
    from analecta.extraction.social import SubstackExtractor
    from analecta.extraction.x import XExtractor
    from analecta.extraction.youtube import YouTubeExtractor

    source_type = detect_source_type(url)
    extractors: dict[SourceType, SourceExtractor] = {
        "article": ArticleExtractor(),
        "youtube": YouTubeExtractor(),
        "substack": SubstackExtractor(),
        "x": XExtractor(),
    }
    return await extractors[source_type].extract(url)
