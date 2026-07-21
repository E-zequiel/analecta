import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest
from youtube_transcript_api._transcripts import FetchedTranscriptSnippet

from analecta.extraction.article import (
    ArticleExtractor,
    _build_from_defuddle,
    _decode_shots,
    _expand_table_spans,
    _has_live_sample_placeholders,
    _is_low_confidence,
    _populate_metadata,
    _readability_class_weight,
    _rescue_linked_lists,
    _rescue_linked_tables,
    _resolve_tier2_url,
    _reunite_intro_with_body,
    _simplify_figure_images,
    _strip_heading_classes,
    _strip_loading_placeholders,
    _try_nextjs_hydration,
    _unwrap_code_examples,
    _unwrap_sections,
)
from analecta.extraction.core import (
    ExtractedContent,
    ExtractionError,
    detect_source_type,
    extract,
)
from analecta.extraction.social import SubstackExtractor, XExtractor
from analecta.extraction.tier2 import Tier2Result
from analecta.extraction.youtube import (
    YouTubeExtractor,
    _extract_video_id,
    _fetch_video_title,
)

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
        ("https://substack.com/inbox/post/123", "substack"),
        ("https://substack.com/p/some-post", "substack"),
        ("https://www.substack.com/p/some-post", "substack"),
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
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_ARTICLE_HTML, "https://example.com/article"),
    )
    result = await ArticleExtractor().extract("https://example.com/article")
    assert result.source_type == "article"
    assert result.url == "https://example.com/article"
    assert len(result.html) > 0


@pytest.mark.asyncio
async def test_article_extractor_readability_fallback(mocker):
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SPARSE_HTML, "https://example.com/sparse"),
    )
    mocker.patch("analecta.extraction.article.trafilatura.extract", return_value=None)
    result = await ArticleExtractor().extract("https://example.com/sparse")
    assert result.source_type == "article"
    assert result.metadata["extractor"] == "readability"


@pytest.mark.asyncio
async def test_article_extractor_raises_on_empty_page(mocker):
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=("<html><body></body></html>", "https://example.com/empty"),
    )
    with pytest.raises(ExtractionError):
        await ArticleExtractor().extract("https://example.com/empty")


@pytest.mark.asyncio
async def test_article_extractor_url_reflects_redirect(mocker):
    """result.url is the post-redirect URL, not the originally requested one."""
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_ARTICLE_HTML, "https://example.com/new-slug"),
    )
    result = await ArticleExtractor().extract("https://example.com/old-slug")
    assert result.url == "https://example.com/new-slug"


# ---------------------------------------------------------------------------
# YouTubeExtractor
# ---------------------------------------------------------------------------

_TRANSCRIPT = [
    FetchedTranscriptSnippet(text="Hello world", start=0.0, duration=1.5),
    FetchedTranscriptSnippet(text="This is a test", start=1.5, duration=2.0),
]


@pytest.mark.asyncio
async def test_youtube_extractor_returns_content(mocker):
    mocker.patch(
        "analecta.extraction.youtube._fetch_video_title",
        return_value=("Rick Astley - Never Gonna Give You Up", "Rick Astley"),
    )
    mocker.patch.object(
        YouTubeExtractor, "_fetch_transcript", return_value=(_TRANSCRIPT, "en")
    )
    result = await YouTubeExtractor().extract("https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert result.title == "Rick Astley - Never Gonna Give You Up"
    assert result.source_type == "youtube"
    assert result.metadata["video_id"] == "dQw4w9WgXcQ"
    assert result.metadata["language"] == "en"
    assert result.metadata["author"] == "Rick Astley"
    assert "Hello world" in result.html


@pytest.mark.asyncio
async def test_youtube_extractor_invalid_url_raises():
    with pytest.raises(ExtractionError, match="Cannot parse video ID"):
        await YouTubeExtractor().extract("https://youtube.com/channel/UC123")


@pytest.mark.asyncio
async def test_youtube_extractor_propagates_extraction_error(mocker):
    mocker.patch(
        "analecta.extraction.youtube._fetch_video_title",
        return_value=("Some Title", None),
    )
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
    mocker.patch(
        "analecta.extraction.youtube._fetch_video_title",
        return_value=("Some Title", None),
    )
    transcript_fr = [
        FetchedTranscriptSnippet(text="Bonjour le monde", start=0.0, duration=1.0),
    ]
    mocker.patch.object(
        YouTubeExtractor, "_fetch_transcript", return_value=(transcript_fr, "fr")
    )
    result = await YouTubeExtractor().extract("https://youtube.com/watch?v=abc123")
    assert result.metadata["language"] == "fr"
    assert "Bonjour" in result.html


@pytest.mark.asyncio
async def test_fetch_video_title_returns_title_and_author(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "title": "Never Gonna Give You Up",
        "author_name": "Rick Astley",
    }
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch(
        "analecta.extraction.youtube.httpx2.AsyncClient", return_value=mock_client
    )
    title, author = await _fetch_video_title("dQw4w9WgXcQ")
    assert title == "Never Gonna Give You Up"
    assert author == "Rick Astley"


@pytest.mark.asyncio
async def test_fetch_video_title_falls_back_on_http_error(mocker):
    import httpx2

    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(
        side_effect=httpx2.HTTPError("timeout")
    )
    mocker.patch(
        "analecta.extraction.youtube.httpx2.AsyncClient", return_value=mock_client
    )
    title, author = await _fetch_video_title("dQw4w9WgXcQ")
    assert title == "YouTube: dQw4w9WgXcQ"
    assert author is None


def test_extract_video_id_strips_timestamp_param():
    url = "https://www.youtube.com/watch?v=zYRkMzgXHgo&t=2s"
    assert _extract_video_id(url) == "zYRkMzgXHgo"


@pytest.mark.asyncio
async def test_youtube_extractor_no_author_when_oembed_missing(mocker):
    mocker.patch(
        "analecta.extraction.youtube._fetch_video_title",
        return_value=("Clean Title", None),
    )
    mocker.patch.object(
        YouTubeExtractor, "_fetch_transcript", return_value=(_TRANSCRIPT, "en")
    )
    result = await YouTubeExtractor().extract("https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert result.title == "Clean Title"
    assert "author" not in result.metadata


# ---------------------------------------------------------------------------
# SubstackExtractor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substack_extractor_returns_substack_type(mocker):
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_ARTICLE_HTML, "https://example.substack.com/p/test"),
    )
    result = await SubstackExtractor().extract("https://example.substack.com/p/test")
    assert result.source_type == "substack"
    assert result.metadata["platform"] == "substack"


@pytest.mark.asyncio
async def test_substack_extractor_resolves_inbox_url(mocker):
    """Inbox URL is resolved via HEAD redirect before extraction."""
    canonical = "https://example.substack.com/p/my-post"
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 302
    mock_resp.headers = {"location": canonical}
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.head = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch(
        "analecta.extraction.social.httpx2.AsyncClient", return_value=mock_client
    )
    mocker.patch.object(
        ArticleExtractor, "_fetch", return_value=(_ARTICLE_HTML, canonical)
    )

    result = await SubstackExtractor().extract("https://substack.com/inbox/post/12345")
    assert result.source_type == "substack"
    assert result.url == canonical


@pytest.mark.asyncio
async def test_substack_extractor_url_reflects_redirect_past_canonical(mocker):
    """A redirect encountered *after* inbox resolution still updates url."""
    canonical = "https://example.substack.com/p/my-post"
    final = "https://example.substack.com/p/my-post-renamed"
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 302
    mock_resp.headers = {"location": canonical}
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.head = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch(
        "analecta.extraction.social.httpx2.AsyncClient", return_value=mock_client
    )
    mocker.patch.object(ArticleExtractor, "_fetch", return_value=(_ARTICLE_HTML, final))

    result = await SubstackExtractor().extract("https://substack.com/inbox/post/12345")
    assert result.url == final


@pytest.mark.asyncio
async def test_substack_extractor_canonical_url_skips_head_request(mocker):
    """Canonical *.substack.com URLs bypass the HEAD redirect step."""
    mock_client_class = mocker.patch("analecta.extraction.social.httpx2.AsyncClient")
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_ARTICLE_HTML, "https://example.substack.com/p/test"),
    )

    await SubstackExtractor().extract("https://example.substack.com/p/test")
    mock_client_class.assert_not_called()


@pytest.mark.asyncio
async def test_substack_extractor_inbox_head_failure_raises(mocker):
    """Network failure on inbox HEAD request raises ExtractionError."""
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.head = mocker.AsyncMock(
        side_effect=Exception("network error")
    )
    mocker.patch(
        "analecta.extraction.social.httpx2.AsyncClient", return_value=mock_client
    )

    with pytest.raises(ExtractionError, match="Could not resolve"):
        await SubstackExtractor().extract("https://substack.com/inbox/post/99")


@pytest.mark.asyncio
async def test_substack_extractor_inbox_no_redirect_raises(mocker):
    """A non-3xx response for an inbox URL raises ExtractionError."""
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.head = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch(
        "analecta.extraction.social.httpx2.AsyncClient", return_value=mock_client
    )

    with pytest.raises(ExtractionError, match="did not redirect"):
        await SubstackExtractor().extract("https://substack.com/inbox/post/99")


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
    mocker.patch(
        "analecta.extraction.youtube._fetch_video_title",
        return_value=("Some Title", None),
    )
    mocker.patch.object(
        YouTubeExtractor, "_fetch_transcript", return_value=(_TRANSCRIPT, "en")
    )
    result = await extract("https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert result.source_type == "youtube"


@pytest.mark.asyncio
async def test_extract_dispatches_article(mocker):
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_ARTICLE_HTML, "https://example.com/article"),
    )
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
# _has_live_sample_placeholders
# ---------------------------------------------------------------------------

_MDN_LIVE_SAMPLE_IFRAME = (
    '<iframe class="sample-code-frame" src="about:blank" '
    'data-live-path="/en-US/docs/Web/CSS/Guides/Box_alignment/Overview/" '
    'data-live-id="grid-align-items" loading="lazy"></iframe>'
)


def test_has_live_sample_placeholders_detects_mdn_iframe():
    html = f"<html><body><main>{_MDN_LIVE_SAMPLE_IFRAME}</main></body></html>"
    assert _has_live_sample_placeholders(html) is True


def test_has_live_sample_placeholders_false_without_data_live_id():
    html = '<iframe class="sample-code-frame" src="about:blank"></iframe>'
    assert _has_live_sample_placeholders(html) is False


def test_has_live_sample_placeholders_false_for_unrelated_iframe():
    html = '<iframe class="other-frame" data-live-id="x"></iframe>'
    assert _has_live_sample_placeholders(html) is False


def test_has_live_sample_placeholders_false_for_plain_article():
    assert _has_live_sample_placeholders(_ARTICLE_HTML) is False


# ---------------------------------------------------------------------------
# _decode_shots
# ---------------------------------------------------------------------------


def test_decode_shots_decodes_valid_base64():
    encoded = base64.b64encode(b"png-bytes").decode()
    assert _decode_shots({"shot-0": encoded}) == {"shot-0": b"png-bytes"}


def test_decode_shots_drops_malformed_entries():
    assert _decode_shots({"shot-0": "not-valid-base64!!"}) == {}


def test_decode_shots_empty_map():
    assert _decode_shots({}) == {}


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
    metadata: dict[str, Any] = {}
    _populate_metadata(metadata, meta)
    assert metadata == {
        "author": "Alice",
        "description": "A post",
        "published": "2024-01-01",
    }


def test_populate_metadata_skips_missing_fields():
    meta = SimpleNamespace(author="Bob", description=None, date=None)
    metadata: dict[str, Any] = {}
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


def test_build_from_defuddle_decodes_shots_into_captured_images():
    encoded = base64.b64encode(b"png-bytes").decode()
    t = Tier2Result(ok=True, content="<p>x</p>", title="T", shots={"shot-0": encoded})
    result = _build_from_defuddle("https://example.com", t)
    assert result.captured_images == {"shot-0": b"png-bytes"}


def test_build_from_defuddle_empty_captured_images_without_shots():
    t = Tier2Result(ok=True, content="<p>x</p>", title="T")
    result = _build_from_defuddle("https://example.com", t)
    assert result.captured_images == {}


# ---------------------------------------------------------------------------
# ArticleExtractor.extract — Tier 2 paths
# ---------------------------------------------------------------------------

_HIGH_CONFIDENCE_WITH_LIVE_SAMPLE = (
    "<html><body><article>"
    + " ".join(["word"] * 250)
    + _MDN_LIVE_SAMPLE_IFRAME
    + "</article></body></html>"
)


@pytest.mark.asyncio
async def test_extract_uses_defuddle_on_low_confidence(mocker):
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SCRIPT_HEAVY, "https://example.com/spa"),
    )
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
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SCRIPT_HEAVY, "https://example.com/spa"),
    )
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
async def test_extract_triggers_tier2_for_live_sample_despite_high_confidence(
    mocker,
):
    """Fires Tier 2 for a page with plenty of real text, purely because of the
    MDN live-sample placeholder — independent of _is_low_confidence."""
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(
            _HIGH_CONFIDENCE_WITH_LIVE_SAMPLE,
            "https://developer.mozilla.org/demo",
        ),
    )
    mock_render = mocker.AsyncMock(
        return_value=Tier2Result(ok=True, content="<p>Rendered</p>", title="D")
    )
    mocker.patch("analecta.extraction.tier2.render_url", new=mock_render)

    result = await ArticleExtractor().extract("https://developer.mozilla.org/demo")

    mock_render.assert_awaited_once()
    assert result.metadata["extractor"] == "defuddle"


@pytest.mark.asyncio
async def test_extract_does_not_trigger_tier2_for_high_confidence_without_live_sample(
    mocker,
):
    raw = "<html><body>" + _200_WORDS + "</body></html>"
    mocker.patch.object(
        ArticleExtractor, "_fetch", return_value=(raw, "https://example.com/article")
    )
    mock_render = mocker.AsyncMock()
    mocker.patch("analecta.extraction.tier2.render_url", new=mock_render)

    await ArticleExtractor().extract("https://example.com/article")

    mock_render.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_skips_tier2_when_disabled_for_low_confidence(
    mocker, monkeypatch
):
    monkeypatch.setenv("ANALECTA_DISABLE_TIER2", "1")
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SCRIPT_HEAVY, "https://example.com/spa"),
    )
    mock_render = mocker.AsyncMock()
    mocker.patch("analecta.extraction.tier2.render_url", new=mock_render)

    result = await ArticleExtractor().extract("https://example.com/spa")

    mock_render.assert_not_awaited()
    assert result.metadata.get("extractor") != "defuddle"


@pytest.mark.asyncio
async def test_extract_skips_tier2_when_disabled_for_live_sample(mocker, monkeypatch):
    monkeypatch.setenv("ANALECTA_DISABLE_TIER2", "true")
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(
            _HIGH_CONFIDENCE_WITH_LIVE_SAMPLE,
            "https://developer.mozilla.org/demo",
        ),
    )
    mock_render = mocker.AsyncMock()
    mocker.patch("analecta.extraction.tier2.render_url", new=mock_render)

    result = await ArticleExtractor().extract("https://developer.mozilla.org/demo")

    mock_render.assert_not_awaited()
    assert result.metadata.get("extractor") != "defuddle"


@pytest.mark.asyncio
async def test_extract_disable_flag_falsy_value_does_not_disable_tier2(
    mocker, monkeypatch
):
    monkeypatch.setenv("ANALECTA_DISABLE_TIER2", "0")
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SCRIPT_HEAVY, "https://example.com/spa"),
    )
    mock_render = mocker.AsyncMock(
        return_value=Tier2Result(ok=True, content="<p>Defuddle</p>", title="D")
    )
    mocker.patch("analecta.extraction.tier2.render_url", new=mock_render)

    result = await ArticleExtractor().extract("https://example.com/spa")

    mock_render.assert_awaited_once()
    assert result.metadata["extractor"] == "defuddle"


@pytest.mark.asyncio
async def test_extract_threads_captured_images_through_outer_html_fallback(mocker):
    encoded = base64.b64encode(b"png-bytes").decode()
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SCRIPT_HEAVY, "https://example.com/spa"),
    )
    mocker.patch(
        "analecta.extraction.tier2.render_url",
        new=mocker.AsyncMock(
            return_value=Tier2Result(
                ok=False,
                outer_html=_ARTICLE_HTML,
                shots={"shot-0": encoded},
            )
        ),
    )
    result = await ArticleExtractor().extract("https://example.com/spa")
    assert result.captured_images == {"shot-0": b"png-bytes"}


@pytest.mark.asyncio
async def test_extract_logs_shot_placeholder_survival_in_defuddle_content(
    mocker, caplog
):
    encoded = base64.b64encode(b"png-bytes").decode()
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SCRIPT_HEAVY, "https://developer.mozilla.org/demo"),
    )
    mocker.patch(
        "analecta.extraction.tier2.render_url",
        new=mocker.AsyncMock(
            return_value=Tier2Result(
                ok=True,
                content='<p><img src="https://analecta-shot.invalid/shot/shot-0.png"></p>',
                title="D",
                shots={"shot-0": encoded, "shot-1": encoded},
            )
        ),
    )
    with caplog.at_level("INFO"):
        result = await ArticleExtractor().extract("https://developer.mozilla.org/demo")

    assert result.captured_images == {"shot-0": b"png-bytes", "shot-1": b"png-bytes"}
    assert "2 shot(s) captured" in caplog.text
    assert "1 placeholder(s) present in content" in caplog.text


@pytest.mark.asyncio
async def test_extract_falls_back_to_tier1_when_render_raises(mocker, caplog):
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_ARTICLE_HTML, "https://example.com/article"),
    )
    mocker.patch(
        "analecta.extraction.tier2.render_url",
        new=mocker.AsyncMock(side_effect=ExtractionError("no server")),
    )
    with caplog.at_level("WARNING"):
        result = await ArticleExtractor().extract("https://example.com/article")
    assert result.source_type == "article"
    assert "Tier 2 render failed" in caplog.text
    assert "no server" in caplog.text


@pytest.mark.asyncio
async def test_extract_uses_defuddle_with_redirect_resolved_url(mocker):
    """Tier 2 gets the original URL, but result.url reflects httpx's resolution."""
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SCRIPT_HEAVY, "https://example.com/final"),
    )
    mock_render = mocker.AsyncMock(
        return_value=Tier2Result(ok=True, content="<p>Defuddle</p>", title="D")
    )
    mocker.patch("analecta.extraction.tier2.render_url", new=mock_render)
    result = await ArticleExtractor().extract("https://example.com/original")
    mock_render.assert_awaited_once_with("https://example.com/original")
    assert result.url == "https://example.com/final"


@pytest.mark.asyncio
async def test_extract_uses_tier2_final_url_over_httpx_when_present(mocker):
    """Browser-reported final_url (JS/redirect-aware) wins over httpx's resolution."""
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_SCRIPT_HEAVY, "https://example.com/httpx-final"),
    )
    mocker.patch(
        "analecta.extraction.tier2.render_url",
        new=mocker.AsyncMock(
            return_value=Tier2Result(
                ok=True,
                content="<p>Defuddle</p>",
                title="D",
                final_url="https://example.com/browser-final",
            )
        ),
    )
    result = await ArticleExtractor().extract("https://example.com/original")
    assert result.url == "https://example.com/browser-final"


# ---------------------------------------------------------------------------
# _resolve_tier2_url
# ---------------------------------------------------------------------------


def test_resolve_tier2_url_prefers_valid_browser_url():
    assert (
        _resolve_tier2_url("https://example.com/final", "https://example.com/httpx")
        == "https://example.com/final"
    )


@pytest.mark.parametrize(
    "bad_url", [None, "", "about:blank", "chrome-error://chromewebdata/"]
)
def test_resolve_tier2_url_falls_back_on_unusable_value(bad_url):
    assert (
        _resolve_tier2_url(bad_url, "https://example.com/httpx")
        == "https://example.com/httpx"
    )


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


def test_simplify_figure_images_unwraps_sole_figure_div():
    # Substack wraps each figure in <div class="captioned-image-container">.
    # That zero-text div gets scored 0 by readability and the figure is dropped.
    # The div must be unwrapped so the figure becomes a direct sibling of prose.
    html = (
        '<div class="body markup">'
        "<p>Intro text.</p>"
        '<div class="captioned-image-container">'
        '<figure><img src="https://cdn.example.com/chart.jpg" alt="chart"/></figure>'
        "</div>"
        "<p>Outro text.</p>"
        "</div>"
    )
    result = _simplify_figure_images(html)
    soup = _BS(result, "html.parser")
    # The wrapper div must be gone; figure is now a sibling of the <p> tags.
    assert soup.find("div", class_="captioned-image-container") is None
    assert soup.find("figure") is not None
    assert soup.find("img") is not None


def test_simplify_figure_images_keeps_div_with_multiple_children():
    # A div that contains a figure AND other content must not be unwrapped.
    html = (
        "<div>"
        '<figure><img src="https://cdn.example.com/img.jpg" alt="x"/></figure>'
        "<p>Caption text alongside the figure.</p>"
        "</div>"
    )
    result = _simplify_figure_images(html)
    soup = _BS(result, "html.parser")
    # Div must remain since it has more than just the figure.
    assert soup.find("div") is not None
    assert soup.find("figure") is not None


# ---------------------------------------------------------------------------
# _rescue_linked_lists
# ---------------------------------------------------------------------------

_BS = __import__("bs4").BeautifulSoup


def test_rescue_linked_lists_inlines_high_density_list():
    # link density ≈ 0.43 → should be converted to sibling <p> elements
    html = (
        "<div>"
        "<p>Intro.</p>"
        "<ul>"
        "<li>✍️ Item one.</li>"
        '<li>🎙️ <a href="https://example.com">'
        "Item two with a long link text here</a>.</li>"
        "<li>🍪 Item three.</li>"
        "</ul>"
        "<p>After.</p>"
        "</div>"
    )
    result = _rescue_linked_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("ul") is None, "high-density <ul> should be dissolved"
    texts = [p.get_text() for p in soup.find_all("p")]
    assert any("Item one" in t for t in texts)
    assert any("Item two" in t for t in texts)
    assert any("Item three" in t for t in texts)


def test_rescue_linked_lists_keeps_low_density_list():
    # no links → density = 0 → <ul> should be preserved
    html = (
        "<ul>"
        "<li>Network usage is at all-time highs.</li>"
        "<li>Fees are near all-time lows.</li>"
        "</ul>"
    )
    result = _rescue_linked_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("ul") is not None


def test_rescue_linked_lists_skips_nav_context():
    html = (
        "<nav>"
        "<ul>"
        "<li><a href='/a'>Link A</a></li>"
        "<li><a href='/b'>Link B</a></li>"
        "</ul>"
        "</nav>"
    )
    result = _rescue_linked_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("ul") is not None, "<ul> inside <nav> must not be touched"


def test_rescue_linked_lists_preserves_anchor_in_converted_p():
    html = (
        "<div>"
        "<ul>"
        '<li><a href="https://example.com">Linked item</a> text.</li>'
        "<li>Plain item.</li>"
        "</ul>"
        "</div>"
    )
    # Craft density > 0.33: link text is "Linked item" (11 chars),
    # total text is "Linked item text.Plain item." (28 chars), density ≈ 0.39
    result = _rescue_linked_lists(html)
    soup = _BS(result, "html.parser")
    # <a> must still be present inside a <p>
    anchor = soup.find("a", href="https://example.com")
    assert anchor is not None
    assert anchor.parent.name == "p"


# _rescue_linked_lists — threshold at 0.2
def test_rescue_linked_lists_density_just_above_threshold():
    # link="ABCDE" (5 chars), rest=" xy" (3 chars) → density=5/8=0.625 > 0.2 → rescued
    html = '<div><ul><li><a href="/x">ABCDE</a> xy</li></ul></div>'
    result = _rescue_linked_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("ul") is None, "High-density list must be dissolved"
    assert soup.find("p") is not None


def test_rescue_linked_lists_density_just_below_threshold():
    # All text is plain (no links) → density = 0 → list kept intact
    html = "<div><ul><li>Plain item one</li><li>Plain item two</li></ul></div>"
    result = _rescue_linked_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("ul") is not None, "Low-density list must be preserved"


# ---------------------------------------------------------------------------
# _rescue_linked_tables
# ---------------------------------------------------------------------------


def test_rescue_linked_tables_flattens_high_density_table():
    # MDN "Specifications"-shape: one header-only row, one row that's ~100%
    # link text -> density well above 0.2 -> table dropped by readability.
    html = (
        '<figure class="table-container"><table>'
        "<thead><tr><th>Specification</th></tr></thead>"
        "<tbody><tr><td>"
        '<a href="https://example.com/spec">Some Spec Level 4</a>'
        "</td></tr></tbody>"
        "</table></figure>"
    )
    result = _rescue_linked_tables(html)
    soup = _BS(result, "html.parser")
    assert soup.find("table") is None
    assert soup.find("figure") is None, "empty wrapper figure should be unwrapped"
    p = soup.find("p")
    assert p is not None
    anchor = p.find("a", href="https://example.com/spec")
    assert anchor is not None
    assert "Specification" not in soup.get_text(), (
        "header-only row must not become a paragraph"
    )


def test_rescue_linked_tables_keeps_low_density_table():
    html = (
        "<table><tr><th>Name</th><th>Value</th></tr>"
        "<tr><td>Some plain descriptive text</td><td>42</td></tr></table>"
    )
    result = _rescue_linked_tables(html)
    soup = _BS(result, "html.parser")
    assert soup.find("table") is not None


def test_rescue_linked_tables_joins_multiple_cells_with_separator():
    html = (
        "<table><tr><th>A</th></tr>"
        '<tr><td><a href="/x">Link text</a></td>'
        '<td><a href="/y">More link text</a></td></tr></table>'
    )
    result = _rescue_linked_tables(html)
    soup = _BS(result, "html.parser")
    p = soup.find("p")
    assert p is not None
    assert " · " in p.get_text()


def test_rescue_linked_tables_skips_positive_weight_table():
    # class="article-table" matches readability's positiveRe -> weight=25,
    # so readability's own "weight < 25" condition never fires regardless of
    # link density -- rescuing this would flatten a table readability keeps.
    html = (
        '<table class="article-table"><tr><th>Spec</th></tr>'
        "<tr><td>"
        '<a href="https://example.com/spec">Some Spec Level 4</a>'
        "</td></tr></table>"
    )
    result = _rescue_linked_tables(html)
    soup = _BS(result, "html.parser")
    assert soup.find("table") is not None, (
        "positive-weight table must survive untouched"
    )


def test_readability_class_weight_matches_real_readability():
    # Cross-checked against readability.readability.Document.class_weight
    # called on the equivalent lxml element (see the memory entry for the
    # exact values): article-table=25, no class=0, sidebar=-25, footer+article=0.
    cases = [
        ('<table class="article-table">', 25),
        ("<table>", 0),
        ('<table class="sidebar">', -25),
        ('<table class="footer article">', 0),
        ('<table id="main-content">', 25),
    ]
    for fragment, expected in cases:
        soup = _BS(fragment + "</table>", "html.parser")
        assert _readability_class_weight(soup.find("table")) == expected, fragment


# ---------------------------------------------------------------------------
# _expand_table_spans
# ---------------------------------------------------------------------------


def test_expand_table_spans_rowspan_shifts_correctly():
    # Regression fixture for the real bug: rowspan on cols 0 and 2 across a
    # group of rows, col 1 varying per row.
    html = (
        "<table><thead><tr><th>Order</th><th>Origin</th><th>Importance</th></tr></thead>"
        "<tbody>"
        '<tr><td rowspan="3">1</td><td>first</td><td rowspan="3">normal</td></tr>'
        "<tr><td>second</td></tr>"
        "<tr><td>third</td></tr>"
        "</tbody></table>"
    )
    result = _expand_table_spans(html)
    soup = _BS(result, "html.parser")
    rows = soup.find_all("tr")
    body_rows = rows[1:]
    assert [c.get_text(strip=True) for c in body_rows[0].find_all(["td", "th"])] == [
        "1",
        "first",
        "normal",
    ]
    assert [c.get_text(strip=True) for c in body_rows[1].find_all(["td", "th"])] == [
        "1",
        "second",
        "normal",
    ]
    assert [c.get_text(strip=True) for c in body_rows[2].find_all(["td", "th"])] == [
        "1",
        "third",
        "normal",
    ]
    # No rowspan/colspan attributes should survive on any cell.
    assert not soup.find_all(attrs={"rowspan": True})
    assert not soup.find_all(attrs={"colspan": True})


def test_expand_table_spans_colspan_duplicates_within_row():
    html = (
        '<table><tr><td colspan="2">wide</td></tr><tr><td>a</td><td>b</td></tr></table>'
    )
    result = _expand_table_spans(html)
    soup = _BS(result, "html.parser")
    rows = soup.find_all("tr")
    assert [c.get_text(strip=True) for c in rows[0].find_all("td")] == ["wide", "wide"]
    assert [c.get_text(strip=True) for c in rows[1].find_all("td")] == ["a", "b"]


def test_expand_table_spans_noop_without_spans():
    html = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
    result = _expand_table_spans(html)
    soup = _BS(result, "html.parser")
    rows = soup.find_all("tr")
    assert [c.get_text(strip=True) for c in rows[0].find_all("td")] == ["a", "b"]
    assert [c.get_text(strip=True) for c in rows[1].find_all("td")] == ["c", "d"]


def test_expand_table_spans_preserves_content_across_multiple_tables():
    html = (
        '<table><tr><td rowspan="2">x</td><td>1</td></tr><tr><td>2</td></tr></table>'
        "<table><tr><td>y</td><td>z</td></tr></table>"
    )
    result = _expand_table_spans(html)
    soup = _BS(result, "html.parser")
    tables = soup.find_all("table")
    assert len(tables) == 2
    first_rows = tables[0].find_all("tr")
    assert [c.get_text(strip=True) for c in first_rows[1].find_all("td")] == ["x", "2"]
    second_rows = tables[1].find_all("tr")
    assert [c.get_text(strip=True) for c in second_rows[0].find_all("td")] == ["y", "z"]


def test_expand_table_spans_survives_readability_link_density_and_min_length():
    # End-to-end regression: confirms the real bug (misaligned columns after
    # readability/markdownify, not a readability drop) by checking the fixed
    # DOM shape directly, since the corruption here is a serialization issue,
    # not a content-density one — readability itself passes this table
    # through unchanged either way.
    from readability import Document

    html = (
        "<html><body><article>"
        "<p>Padding paragraph to give the surrounding article region enough "
        "weight for readability to select it as the main content candidate.</p>"
        "<table><thead><tr><th>Order</th><th>Origin</th><th>Importance</th></tr></thead>"
        "<tbody>"
        '<tr><td rowspan="2">1</td><td>first</td><td rowspan="2">normal</td></tr>'
        "<tr><td>second</td></tr>"
        "</tbody></table>"
        "<p>Another padding paragraph, also long enough to keep this region "
        "scored as the main content by readability's own algorithm.</p>"
        "</article></body></html>"
    )
    fixed_html = _expand_table_spans(html)
    summary = Document(fixed_html).summary() or ""
    soup = _BS(summary, "html.parser")
    rows = soup.find_all("tr")
    body_rows = [r for r in rows if r.find("th") is None]
    assert [c.get_text(strip=True) for c in body_rows[1].find_all("td")] == [
        "1",
        "second",
        "normal",
    ]


# ---------------------------------------------------------------------------
# _unwrap_code_examples
# ---------------------------------------------------------------------------


def test_unwrap_code_examples_hoists_bare_pre_and_drops_wrapper():
    # MDN's shape: a short one-line snippet whose wrapper (header label +
    # code) is under readability's 25-char min_text_length — the wrapper div
    # would otherwise be silently dropped as "too short content, no image".
    html = (
        "<p>Some text.</p>"
        '<div class="code-example">'
        '<div class="example-header"><span class="language-name">css</span></div>'
        '<pre class="brush: css notranslate"><code>margin-left: 3px;</code></pre>'
        "</div>"
        "<p>More text.</p>"
    )
    result = _unwrap_code_examples(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div", class_="code-example") is None
    assert soup.find("div", class_="example-header") is None
    pre = soup.find("pre")
    assert pre is not None
    assert "margin-left: 3px;" in pre.get_text()


def test_unwrap_code_examples_preserves_language_class():
    html = (
        '<div class="code-example">'
        '<div class="example-header"><span class="language-name">css</span></div>'
        '<pre class="brush: css notranslate"><code>li { margin-left: 0; }</code></pre>'
        "</div>"
    )
    result = _unwrap_code_examples(html)
    soup = _BS(result, "html.parser")
    pre = soup.find("pre")
    assert "brush:" in pre.get("class", [])
    assert "css" in pre.get("class", [])


def test_unwrap_code_examples_handles_multiple_wrappers():
    html = (
        '<div class="code-example">'
        '<pre class="brush: css">a { color: red; }</pre>'
        "</div>"
        '<div class="code-example">'
        '<pre class="brush: html">&lt;div&gt;&lt;/div&gt;</pre>'
        "</div>"
    )
    result = _unwrap_code_examples(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div", class_="code-example") is None
    assert len(soup.find_all("pre")) == 2


def test_unwrap_code_examples_noop_without_wrapper():
    html = "<p>Intro.</p><pre><code>plain snippet</code></pre>"
    result = _unwrap_code_examples(html)
    soup = _BS(result, "html.parser")
    assert soup.find("pre") is not None
    assert soup.find("p") is not None


def test_unwrap_code_examples_noop_wrapper_without_pre():
    # A code-example-classed div with no <pre> inside (shouldn't happen on
    # MDN, but the function must not raise or delete unrelated content).
    html = '<div class="code-example"><p>No code here.</p></div>'
    result = _unwrap_code_examples(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div", class_="code-example") is not None
    assert soup.find("p") is not None


def test_unwrap_code_examples_survives_readability_min_text_length():
    # Regression test for the actual bug: readability-lxml's sanitize() drops
    # any div/table/ul/aside/header/footer/section under min_text_length (25
    # chars, default) with no <img> — MDN's wrapper for a bare one-line
    # declaration falls under that threshold. Confirms the fix defeats the
    # heuristic end to end, not just that the DOM shape changes.
    from readability import Document

    html = (
        "<html><body><article>"
        "<p>Padding paragraph one to give the surrounding article enough "
        "weight for readability to pick it as the main candidate region.</p>"
        "<p>We then look at order of appearance. The second one wins.</p>"
        '<div class="code-example">'
        '<div class="example-header"><span class="language-name">css</span></div>'
        '<pre class="brush: css notranslate"><code>margin-left: 3px;</code></pre>'
        "</div>"
        "<p>Padding paragraph two, also long enough to keep this region "
        "scored as the main content candidate for readability's algorithm.</p>"
        "</article></body></html>"
    )

    without_fix = Document(html).summary() or ""
    assert "margin-left: 3px;" not in without_fix, (
        "fixture doesn't reproduce the readability drop — adjust padding"
    )

    fixed_html = _unwrap_code_examples(html)
    with_fix = Document(fixed_html).summary() or ""
    assert "margin-left: 3px;" in with_fix


# _strip_loading_placeholders
def test_strip_loading_placeholders_removes_loading_p():
    html = "<div><p>Loading...</p><p>Real content here.</p></div>"
    result = _strip_loading_placeholders(html)
    soup = _BS(result, "html.parser")
    paragraphs = soup.find_all("p")
    texts = [p.get_text() for p in paragraphs]
    assert "Loading..." not in texts
    assert "Real content here." in texts


def test_strip_loading_placeholders_removes_loading_with_ellipsis():
    html = "<div><p>Loading affected packages…</p></div>"
    result = _strip_loading_placeholders(html)
    soup = _BS(result, "html.parser")
    assert soup.find("p") is None


def test_strip_loading_placeholders_keeps_element_with_extra_content():
    # Full sentence starting with "Loading" must NOT be removed
    html = "<div><p>Loading is an important topic in engineering.</p></div>"
    result = _strip_loading_placeholders(html)
    soup = _BS(result, "html.parser")
    assert soup.find("p") is not None


def test_strip_loading_placeholders_removes_span():
    html = "<div><span>Loading</span><p>Article text.</p></div>"
    result = _strip_loading_placeholders(html)
    soup = _BS(result, "html.parser")
    assert soup.find("span") is None
    assert soup.find("p") is not None


# ---------------------------------------------------------------------------
# _unwrap_sections
# ---------------------------------------------------------------------------


def test_unwrap_sections_removes_section_tag():
    html = "<section><h2>Title</h2><p>Content.</p></section>"
    result = _unwrap_sections(html)
    soup = _BS(result, "html.parser")
    assert soup.find("section") is None
    assert soup.find("h2") is not None
    assert soup.find("p") is not None


def test_unwrap_sections_flattens_nested_sections():
    # Wikipedia nests sections 3 levels deep; all must be unwrapped.
    html = (
        "<section>"
        "<section>"
        "<section>"
        "<h4>Deep Heading</h4>"
        "<p>Deep content.</p>"
        "</section>"
        "</section>"
        "</section>"
    )
    result = _unwrap_sections(html)
    soup = _BS(result, "html.parser")
    assert soup.find("section") is None
    assert soup.find("h4") is not None
    assert soup.find("p") is not None


def test_unwrap_sections_noop_when_no_sections():
    html = "<div><h2>Title</h2><p>Content.</p></div>"
    result = _unwrap_sections(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div") is not None
    assert soup.find("h2") is not None


def test_unwrap_sections_preserves_all_content():
    html = (
        '<section data-mw-section-id="1">'
        "<h2>Top</h2><p>Para one.</p>"
        '<section data-mw-section-id="2">'
        "<h3>Sub</h3><p>Para two.</p>"
        "</section>"
        "</section>"
    )
    result = _unwrap_sections(html)
    soup = _BS(result, "html.parser")
    assert soup.find("section") is None
    assert soup.find("h2") is not None
    assert soup.find("h3") is not None
    assert len(soup.find_all("p")) == 2


# ---------------------------------------------------------------------------
# _reunite_intro_with_body
# ---------------------------------------------------------------------------


def test_reunite_intro_with_body_mdn_shape_moves_paragraphs():
    # MDN: <h1>+intro <p>s in one div, a TOC <aside> in between, real body
    # in a sibling div — 3 siblings under <main>. readability scores the
    # low-text intro div separately from the body and drops it.
    html = (
        "<main>"
        '<div class="header"><h1>Title</h1>'
        "<p>Intro sentence one.</p><p>Intro sentence two.</p></div>"
        '<aside class="toc">Table of contents</aside>'
        '<div class="body"><h2>Section</h2><p>Body content.</p></div>'
        "</main>"
    )
    result = _reunite_intro_with_body(html)
    soup = _BS(result, "html.parser")
    header = soup.find("div", class_="header")
    body = soup.find("div", class_="body")
    assert [c.name for c in header.find_all(recursive=False)] == ["h1"]
    body_children = body.find_all(recursive=False)
    assert [c.get_text(strip=True) for c in body_children[:2]] == [
        "Intro sentence one.",
        "Intro sentence two.",
    ]
    # Original order preserved, and body's own content still follows.
    assert body_children[2].name == "h2"


def test_reunite_intro_with_body_moves_interleaved_list():
    # MDN CSS Inheritance shape: a <ul> sits between two intro <p>s. Moving
    # only <p> tags orphans the <ul> in the low-scoring header div, where
    # readability drops it. All three must move, in original order.
    html = (
        "<main>"
        '<div class="header"><h1>Title</h1>'
        "<p>Intro sentence.</p>"
        "<p>Categorized in two types:</p>"
        "<ul><li>inherited properties</li><li>non-inherited properties</li></ul>"
        "<p>Trailing sentence.</p>"
        "</div>"
        '<div class="body"><h2>Section</h2><p>Body content.</p></div>'
        "</main>"
    )
    result = _reunite_intro_with_body(html)
    soup = _BS(result, "html.parser")
    header = soup.find("div", class_="header")
    body = soup.find("div", class_="body")
    assert [c.name for c in header.find_all(recursive=False)] == ["h1"]
    body_children = body.find_all(recursive=False)
    assert [c.name for c in body_children[:4]] == ["p", "p", "ul", "p"]
    assert body_children[2].find_all("li")[0].get_text(strip=True) == (
        "inherited properties"
    )
    # Original order preserved, and body's own content still follows.
    assert body_children[4].name == "h2"


def test_reunite_intro_with_body_works_without_intervening_aside():
    # Same shape but header/body are directly adjacent siblings (no <aside>).
    html = (
        "<main>"
        "<div><h1>Title</h1><p>Intro.</p></div>"
        "<div><h2>Section</h2><p>Body content.</p></div>"
        "</main>"
    )
    result = _reunite_intro_with_body(html)
    soup = _BS(result, "html.parser")
    divs = soup.find("main").find_all("div", recursive=False)
    assert divs[0].find("p") is None
    assert divs[1].find_all(recursive=False)[0].get_text(strip=True) == "Intro."


def test_reunite_intro_with_body_noop_with_multiple_sibling_candidates():
    # Substack shape: h1's parent has 2 non-chrome sibling divs (a visibility
    # check + a buttons container), not the single real body — must not
    # guess which one is the real content.
    html = (
        "<article>"
        "<div><h1>Title</h1><p>Intro.</p></div>"
        "<div>Visibility check</div>"
        "<div>Buttons</div>"
        "</article>"
    )
    result = _reunite_intro_with_body(html)
    soup = _BS(result, "html.parser")
    intro = soup.find("h1").parent
    assert intro.find("p") is not None


def test_reunite_intro_with_body_noop_when_intro_has_no_paragraphs():
    # h1's own container has no <p> children to move (e.g. a masthead h1)
    # even though exactly one sibling candidate exists.
    html = (
        "<article>"
        "<div><h1>Site Name</h1></div>"
        "<div><h2>Real content</h2><p>Body.</p></div>"
        "</article>"
    )
    result = _reunite_intro_with_body(html)
    soup = _BS(result, "html.parser")
    body_div = soup.find_all("div")[1]
    assert [c.name for c in body_div.find_all(recursive=False)] == ["h2", "p"]


def test_reunite_intro_with_body_noop_when_no_h1():
    html = "<main><div><h2>No h1 here</h2></div></main>"
    result = _reunite_intro_with_body(html)
    assert result == str(_BS(html, "html.parser"))


def test_reunite_intro_with_body_prefers_main_scope_over_masthead_h1():
    # A masthead <h1> outside <main> must not be mistaken for the article's
    # own h1 — real MDN/Substack pages can carry a site-branding h1.
    html = (
        "<body>"
        "<h1>Site Name</h1>"
        "<main><div><h1>Article Title</h1><p>Intro.</p></div>"
        "<div><h2>Body</h2><p>Content.</p></div></main>"
        "</body>"
    )
    result = _reunite_intro_with_body(html)
    soup = _BS(result, "html.parser")
    main = soup.find("main")
    intro = main.find("h1").parent
    assert intro.find("p") is None


# ---------------------------------------------------------------------------
# _strip_heading_classes
# ---------------------------------------------------------------------------


def test_strip_heading_classes_removes_class_from_headings():
    html = '<h2 class="header-anchor-post">Title</h2>'
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    h2 = soup.find("h2")
    assert h2 is not None
    assert not h2.get("class")
    assert h2.get_text(strip=True) == "Title"


def test_strip_heading_classes_removes_empty_inner_div():
    # Substack injects an anchor-button div inside each heading.
    html = (
        '<h2 class="header-anchor-post">'
        "Section Title"
        '<div class="header-anchor-parent">'
        '<a href="#section"><button></button></a></div>'
        "</h2>"
    )
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    h2 = soup.find("h2")
    assert h2 is not None
    assert h2.find("div") is None
    assert h2.get_text(strip=True) == "Section Title"


def test_strip_heading_classes_preserves_text_bearing_inner_elements():
    # An inner <a> with real text (e.g. a linked heading) must not be removed.
    html = '<h3 class="some-class"><a href="/slug">Linked Heading</a></h3>'
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    h3 = soup.find("h3")
    assert h3 is not None
    assert h3.find("a") is not None
    assert h3.get_text(strip=True) == "Linked Heading"


def test_strip_heading_classes_noop_on_non_heading_elements():
    # Classes on <p>, <div>, etc. must remain untouched.
    html = '<div class="header-anchor-post"><p class="lead">Text</p></div>'
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div", class_="header-anchor-post") is not None
    assert soup.find("p", class_="lead") is not None


def test_strip_heading_classes_all_heading_levels_stripped():
    tags = ["h1", "h2", "h3", "h4", "h5", "h6"]
    html = "".join(f'<{t} class="utility-class">Text</{t}>' for t in tags)
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    for tag in tags:
        el = soup.find(tag)
        assert el is not None
        assert not el.get("class"), f"{tag} still has class attribute"


def test_strip_heading_classes_unwraps_div_with_link_only_sibling():
    # Wikipedia Vector 2022: each heading lives in a <div class="mw-heading">
    # alongside a <span class="mw-editsection">[edit]</span>.
    # The edit link inflates the div's link density; readability drops the div.
    html = (
        '<div class="mw-heading mw-heading2">'
        '<h2 id="Biography">Biography</h2>'
        '<span class="mw-editsection">'
        '<span>[</span><a href="/w/index.php?action=edit">edit</a><span>]</span>'
        "</span>"
        "</div>"
    )
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    # Wrapper div must be gone; h2 survives as a direct element.
    assert soup.find("div", class_="mw-heading") is None
    h2 = soup.find("h2")
    assert h2 is not None
    assert h2.get_text(strip=True) == "Biography"
    # Edit span must also be gone.
    assert soup.find("span", class_="mw-editsection") is None


def test_strip_heading_classes_keeps_div_with_prose_sibling():
    # A div whose sibling has real prose must NOT be unwrapped.
    html = (
        "<div>"
        "<h2>Title</h2>"
        "<p>A subtitle or introductory sentence below the heading.</p>"
        "</div>"
    )
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div") is not None
    assert soup.find("h2") is not None
    assert soup.find("p") is not None


def test_strip_heading_classes_preserves_figure_sibling():
    # A figure sitting next to a heading in the same div must not be removed.
    # (Regression guard for the Substack captioned-image-container pattern.)
    html = (
        "<div>"
        '<h2 class="section-title">Chart Section</h2>'
        '<figure><img src="https://cdn.example.com/chart.png" alt="chart"/></figure>'
        "</div>"
    )
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    assert soup.find("figure") is not None
    assert soup.find("img") is not None


def test_strip_heading_classes_unwraps_self_referencing_permalink_anchor():
    # MDN wraps the entire heading text in a hover-permalink anchor whose
    # href points back at the heading's own id. readability/trafilatura
    # treat an all-link heading as boilerplate and drop it whole.
    html = (
        '<h2 id="key_concepts" class="heading">'
        '<a class="heading-anchor" href="#key_concepts">Key concepts</a>'
        "</h2>"
    )
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    h2 = soup.find("h2")
    assert h2 is not None
    assert h2.find("a") is None
    assert h2.get_text(strip=True) == "Key concepts"


def test_strip_heading_classes_keeps_anchor_when_href_targets_different_id():
    # Same shape, but the href does NOT match the heading's own id — a
    # genuine cross-reference link, not a self-referencing permalink icon.
    html = '<h2 id="alpha"><a href="#beta">Alpha</a></h2>'
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    h2 = soup.find("h2")
    assert h2 is not None
    assert h2.find("a") is not None
    assert h2.get_text(strip=True) == "Alpha"


def test_strip_heading_classes_keeps_self_referencing_anchor_when_heading_has_no_id():
    # No id on the heading at all — nothing for the anchor to self-reference.
    html = '<h2><a href="#key_concepts">Key concepts</a></h2>'
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    h2 = soup.find("h2")
    assert h2 is not None
    assert h2.find("a") is not None


def test_strip_heading_classes_ignores_comment_when_counting_meaningful_children():
    # MDN hydration comments (e.g. Lit's <!--lit-node 1-->) are NavigableString
    # subclasses in BeautifulSoup and must not be miscounted as a second
    # meaningful child, which would block the permalink-anchor unwrap.
    html = (
        '<h2 id="key_concepts">'
        "<!--lit-node 1-->"
        '<a href="#key_concepts">Key concepts</a>'
        "</h2>"
    )
    result = _strip_heading_classes(html)
    soup = _BS(result, "html.parser")
    h2 = soup.find("h2")
    assert h2 is not None
    assert h2.find("a") is None
    assert h2.get_text(strip=True) == "Key concepts"
