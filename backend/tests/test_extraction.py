import json
from types import SimpleNamespace

import pytest
from youtube_transcript_api._transcripts import FetchedTranscriptSnippet

from analecta.extraction.article import (
    ArticleExtractor,
    _build_from_defuddle,
    _is_low_confidence,
    _populate_metadata,
    _simplify_figure_images,
    _try_nextjs_hydration,
)
from analecta.extraction.core import (
    ExtractedContent,
    ExtractionError,
    detect_source_type,
    extract,
)
from analecta.extraction.social import SubstackExtractor, XExtractor
from analecta.extraction.tier2 import Tier2Result
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


# ---------------------------------------------------------------------------
# _is_low_confidence
# ---------------------------------------------------------------------------

_200_WORDS = "<article>" + " ".join(["word"] * 200) + "</article>"
# 5 scripts + short article body: density > 0.4, < 200 words, ≥ MIN_CONTENT_LEN.
_SCRIPT_HEAVY = (
    "<html><body>"
    + "<script>x=1;</script>" * 5
    + "<article><p>"
    + ("word " * 30)
    + "</p></article>"
    + "</body></html>"
)


def test_is_low_confidence_short_extracted():
    assert (
        _is_low_confidence("<html><body><p>short</p></body></html>", "<p>short</p>")
        is True
    )


def test_is_low_confidence_script_heavy_raw():
    assert _is_low_confidence(_SCRIPT_HEAVY, _200_WORDS) is True


def test_is_low_confidence_normal_content():
    raw = "<html><body>" + _200_WORDS + "</body></html>"
    assert _is_low_confidence(raw, _200_WORDS) is False


# ---------------------------------------------------------------------------
# _try_nextjs_hydration
# ---------------------------------------------------------------------------

_NEXT_DATA_ENOUGH = json.dumps({"props": {"pageProps": {"body": "word " * 210}}})
_NEXTJS_HTML_ENOUGH = (
    f'<html><body><script id="__NEXT_DATA__" type="application/json">'
    f"{_NEXT_DATA_ENOUGH}</script></body></html>"
)

_NEXT_DATA_FEW = json.dumps({"props": {"pageProps": {"body": "tiny"}}})
_NEXTJS_HTML_FEW = (
    f'<html><body><script id="__NEXT_DATA__" type="application/json">'
    f"{_NEXT_DATA_FEW}</script></body></html>"
)


def test_try_nextjs_hydration_plain_html_returns_none():
    assert _try_nextjs_hydration("<html><body><p>plain</p></body></html>") is None


def test_try_nextjs_hydration_next_data_enough_words():
    result = _try_nextjs_hydration(_NEXTJS_HTML_ENOUGH)
    assert result is not None
    assert "word" in result


def test_try_nextjs_hydration_next_data_too_few_words():
    assert _try_nextjs_hydration(_NEXTJS_HTML_FEW) is None


# App Router RSC payload chunks must never be used as article content.
# The __next_f scripts contain wire-format protocol markers, not readable text.
_RSC_PAYLOAD_HTML = (
    "<html><body>"
    '<script>self.__next_f.push([1,"$Sreact.fragment"])</script>'
    '<script>self.__next_f.push([1,"I[237420,[\\"chunk.js\\"],\\"GoogleTagManager\\"]"])</script>'
    + ('<script>self.__next_f.push([1,"word "])</script>' * 210)
    + "</body></html>"
)


def test_try_nextjs_hydration_rsc_app_router_returns_none():
    """RSC App Router pages must not short-circuit to RSC wire-format content."""
    assert _try_nextjs_hydration(_RSC_PAYLOAD_HTML) is None


# ---------------------------------------------------------------------------
# _populate_metadata
# ---------------------------------------------------------------------------


def test_populate_metadata_fills_all_fields():
    meta = SimpleNamespace(author="Alice", description="A post", date="2024-01-01")
    metadata: dict = {}
    _populate_metadata(metadata, meta)
    assert metadata == {
        "author": "Alice",
        "description": "A post",
        "published": "2024-01-01",
    }


def test_populate_metadata_skips_missing_fields():
    meta = SimpleNamespace(author="Bob", description=None, date=None)
    metadata: dict = {}
    _populate_metadata(metadata, meta)
    assert metadata == {"author": "Bob"}
    assert "description" not in metadata
    assert "published" not in metadata


# ---------------------------------------------------------------------------
# _build_from_defuddle
# ---------------------------------------------------------------------------


def test_build_from_defuddle_constructs_content():
    t = Tier2Result(
        ok=True,
        content="<p>Extracted</p>",
        title="The Title",
        author="Eve",
        description="Short desc",
        published="2024-06-01",
    )
    result = _build_from_defuddle("https://example.com", t)
    assert result.title == "The Title"
    assert result.html == "<p>Extracted</p>"
    assert result.url == "https://example.com"
    assert result.source_type == "article"
    assert result.metadata["extractor"] == "defuddle"
    assert result.metadata["author"] == "Eve"
    assert result.metadata["published"] == "2024-06-01"


# ---------------------------------------------------------------------------
# ArticleExtractor.extract — Tier 2 paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_uses_defuddle_on_low_confidence(mocker):
    mocker.patch.object(ArticleExtractor, "_fetch", return_value=_SCRIPT_HEAVY)
    mocker.patch(
        "analecta.extraction.tier2.render_url",
        new=mocker.AsyncMock(
            return_value=Tier2Result(ok=True, content="<p>Defuddle</p>", title="D")
        ),
    )
    result = await ArticleExtractor().extract("https://example.com/spa")
    assert result.metadata["extractor"] == "defuddle"
    assert "Defuddle" in result.html


@pytest.mark.asyncio
async def test_extract_uses_outer_html_when_defuddle_fails(mocker):
    mocker.patch.object(ArticleExtractor, "_fetch", return_value=_SCRIPT_HEAVY)
    mocker.patch(
        "analecta.extraction.tier2.render_url",
        new=mocker.AsyncMock(
            return_value=Tier2Result(ok=False, outer_html=_ARTICLE_HTML)
        ),
    )
    result = await ArticleExtractor().extract("https://example.com/spa")
    assert result.source_type == "article"
    assert result.metadata.get("extractor") != "defuddle"


@pytest.mark.asyncio
async def test_extract_falls_back_to_tier1_when_render_raises(mocker):
    mocker.patch.object(ArticleExtractor, "_fetch", return_value=_ARTICLE_HTML)
    mocker.patch(
        "analecta.extraction.tier2.render_url",
        new=mocker.AsyncMock(side_effect=ExtractionError("no server")),
    )
    result = await ArticleExtractor().extract("https://example.com/article")
    assert result.source_type == "article"


# ---------------------------------------------------------------------------
# _simplify_figure_images
# ---------------------------------------------------------------------------


def test_simplify_figure_images_hoists_img_to_figure():
    html = (
        "<figure>"
        '<div class="text-center">'
        '<a href="https://example.com">'
        '<img src="https://cdn.example.com/img.jpg" alt="photo"/>'
        "</a>"
        "</div>"
        "</figure>"
    )
    result = _simplify_figure_images(html)
    soup = __import__("bs4").BeautifulSoup(result, "html.parser")
    fig = soup.find("figure")
    assert fig is not None
    # img must be a direct child of figure
    assert fig.find("img", recursive=False) is not None
    assert fig.find("img")["src"] == "https://cdn.example.com/img.jpg"


def test_simplify_figure_images_preserves_figcaption():
    html = (
        "<figure>"
        '<div><img src="https://cdn.example.com/img.jpg" alt="photo"/></div>'
        "<figcaption>A caption</figcaption>"
        "</figure>"
    )
    result = _simplify_figure_images(html)
    soup = __import__("bs4").BeautifulSoup(result, "html.parser")
    fig = soup.find("figure")
    assert fig.find("figcaption") is not None
    assert fig.find("figcaption").get_text() == "A caption"


def test_simplify_figure_images_noop_when_no_figures():
    html = "<p>No figures here.</p>"
    result = _simplify_figure_images(html)
    assert "No figures here." in result


def test_simplify_figure_images_noop_when_img_already_direct():
    html = '<figure><img src="https://cdn.example.com/img.jpg" alt="x"/></figure>'
    result = _simplify_figure_images(html)
    soup = __import__("bs4").BeautifulSoup(result, "html.parser")
    assert soup.find("img") is not None
