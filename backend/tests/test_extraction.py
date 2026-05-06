import pytest
from youtube_transcript_api._transcripts import FetchedTranscriptSnippet

from analecta.extraction.article import ArticleExtractor
from analecta.extraction.core import (
    ExtractedContent,
    ExtractionError,
    detect_source_type,
    extract,
)
from analecta.extraction.social import SubstackExtractor, XExtractor
from analecta.extraction.youtube import YouTubeExtractor, _extract_video_id

# ---------------------------------------------------------------------------
# Sample HTML that trafilatura can extract from
# ---------------------------------------------------------------------------

_ARTICLE_HTML = """
<html>
<head><title>Test Article</title></head>
<body>
<article>
<h1>Test Article</h1>
<p>This is a substantial article body with enough content for trafilatura
to consider it worth extracting. It needs to be long enough to pass the
internal quality threshold. Adding more sentences here to ensure that
the minimum length is met by the extractor under test.</p>
</article>
</body>
</html>
"""

_SPARSE_HTML = "<html><body><div>" + "<p>word</p>" * 30 + "</div></body></html>"

# ---------------------------------------------------------------------------
# detect_source_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtube.com/watch?v=abc", "youtube"),
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://m.youtube.com/watch?v=abc", "youtube"),
        ("https://x.com/user/status/1", "x"),
        ("https://twitter.com/user/status/1", "x"),
        ("https://www.x.com/user/status/1", "x"),
        ("https://example.substack.com/p/post", "substack"),
        ("https://example.com/article", "article"),
        ("https://blog.example.org/post", "article"),
    ],
)
def test_detect_source_type(url, expected):
    assert detect_source_type(url) == expected


# ---------------------------------------------------------------------------
# _extract_video_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?list=PL&v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://example.com/not-a-video", None),
        ("https://youtube.com/channel/UC123", None),
    ],
)
def test_extract_video_id(url, expected):
    assert _extract_video_id(url) == expected


# ---------------------------------------------------------------------------
# ExtractedContent
# ---------------------------------------------------------------------------


def test_extracted_content_defaults():
    ec = ExtractedContent(
        title="T", html="<p>h</p>", url="https://a.com", source_type="article"
    )
    assert ec.metadata == {}


# ---------------------------------------------------------------------------
# ArticleExtractor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_article_extractor_trafilatura_path(mocker):
    mocker.patch.object(ArticleExtractor, "_fetch", return_value=_ARTICLE_HTML)
    result = await ArticleExtractor().extract("https://example.com/article")
    assert result.source_type == "article"
    assert result.url == "https://example.com/article"
    assert len(result.html) > 0


@pytest.mark.asyncio
async def test_article_extractor_readability_fallback(mocker):
    mocker.patch.object(ArticleExtractor, "_fetch", return_value=_SPARSE_HTML)
    mocker.patch("analecta.extraction.article.trafilatura.extract", return_value=None)
    result = await ArticleExtractor().extract("https://example.com/sparse")
    assert result.source_type == "article"
    assert result.metadata["extractor"] == "readability"


@pytest.mark.asyncio
async def test_article_extractor_raises_on_empty_page(mocker):
    mocker.patch.object(
        ArticleExtractor, "_fetch", return_value="<html><body></body></html>"
    )
    with pytest.raises(ExtractionError):
        await ArticleExtractor().extract("https://example.com/empty")


# ---------------------------------------------------------------------------
# YouTubeExtractor
# ---------------------------------------------------------------------------

_TRANSCRIPT = [
    FetchedTranscriptSnippet(text="Hello world", start=0.0, duration=1.5),
    FetchedTranscriptSnippet(text="This is a test", start=1.5, duration=2.0),
]


@pytest.mark.asyncio
async def test_youtube_extractor_returns_content(mocker):
    mocker.patch.object(
        YouTubeExtractor, "_fetch_transcript", return_value=(_TRANSCRIPT, "en")
    )
    result = await YouTubeExtractor().extract("https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert result.source_type == "youtube"
    assert result.metadata["video_id"] == "dQw4w9WgXcQ"
    assert result.metadata["language"] == "en"
    assert "Hello world" in result.html


@pytest.mark.asyncio
async def test_youtube_extractor_invalid_url_raises():
    with pytest.raises(ExtractionError, match="Cannot parse video ID"):
        await YouTubeExtractor().extract("https://youtube.com/channel/UC123")


@pytest.mark.asyncio
async def test_youtube_extractor_propagates_extraction_error(mocker):
    mocker.patch.object(
        YouTubeExtractor,
        "_fetch_transcript",
        side_effect=ExtractionError("No transcript"),
    )
    with pytest.raises(ExtractionError):
        await YouTubeExtractor().extract("https://youtube.com/watch?v=dQw4w9WgXcQ")


@pytest.mark.asyncio
async def test_youtube_extractor_falls_back_to_any_language(mocker):
    """When en/es are unavailable, the first available transcript is used."""
    transcript_fr = [
        FetchedTranscriptSnippet(text="Bonjour le monde", start=0.0, duration=1.0),
    ]
    mocker.patch.object(
        YouTubeExtractor, "_fetch_transcript", return_value=(transcript_fr, "fr")
    )
    result = await YouTubeExtractor().extract("https://youtube.com/watch?v=abc123")
    assert result.metadata["language"] == "fr"
    assert "Bonjour" in result.html


# ---------------------------------------------------------------------------
# SubstackExtractor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substack_extractor_returns_substack_type(mocker):
    mocker.patch.object(ArticleExtractor, "_fetch", return_value=_ARTICLE_HTML)
    result = await SubstackExtractor().extract("https://example.substack.com/p/test")
    assert result.source_type == "substack"
    assert result.metadata["platform"] == "substack"


# ---------------------------------------------------------------------------
# XExtractor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x_extractor_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        await XExtractor().extract("https://x.com/user/status/123")


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_dispatches_youtube(mocker):
    mocker.patch.object(
        YouTubeExtractor, "_fetch_transcript", return_value=(_TRANSCRIPT, "en")
    )
    result = await extract("https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert result.source_type == "youtube"


@pytest.mark.asyncio
async def test_extract_dispatches_article(mocker):
    mocker.patch.object(ArticleExtractor, "_fetch", return_value=_ARTICLE_HTML)
    result = await extract("https://example.com/article")
    assert result.source_type == "article"


@pytest.mark.asyncio
async def test_extract_dispatches_x_raises():
    with pytest.raises(NotImplementedError):
        await extract("https://x.com/user/status/123")
