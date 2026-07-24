import difflib
import json
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from bs4 import BeautifulSoup, Tag
from youtube_transcript_api._transcripts import FetchedTranscriptSnippet

from analecta.extraction.article import (
    _HERO_ALT_MATCH_RATIO,
    ArticleExtractor,
    _expand_table_spans,
    _find_dek_paragraph,
    _find_hero_image,
    _is_low_confidence,
    _populate_metadata,
    _readability_class_weight,
    _rescue_linked_lists,
    _rescue_linked_tables,
    _rescue_orphaned_header,
    _rescue_short_figure_labels,
    _rescue_short_nested_lists,
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
from analecta.extraction.social import SubstackExtractor
from analecta.extraction.tweet_embeds import (
    _iframe_fallback_link,
    _iframe_tweet_id,
    _permalink_from_blockquote,
    _reshape_fallback_blockquote,
    resolve_embedded_tweets,
)
from analecta.extraction.x import (
    XExtractor,
    _author_line_html,
    _clean_tweet_text,
    _display_text,
    _extract_tweet_id,
    _fetch_oembed,
    _fetch_syndication,
    _render_tweet_html,
    _syndication_token,
    _utf16_offset_to_codepoint,
    _walk_reply_chain,
)
from analecta.extraction.youtube import (
    YouTubeExtractor,
    _extract_video_id,
    _fetch_video_title,
)
from analecta.markdown.backlinks import parse_refs

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
        ("https://mobile.twitter.com/user/status/1", "x"),
        ("https://mobile.x.com/user/status/1", "x"),
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


@pytest.mark.asyncio
async def test_article_extractor_sets_low_confidence_true_for_thin_content(mocker):
    """low_confidence is a frontmatter-only diagnostic signal, always
    attached to metadata rather than triggering anything."""
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(_ARTICLE_HTML, "https://example.com/article"),
    )
    result = await ArticleExtractor().extract("https://example.com/article")
    assert result.metadata["low_confidence"] is True


@pytest.mark.asyncio
async def test_article_extractor_sets_low_confidence_false_for_substantial_content(
    mocker,
):
    raw = "<html><body>" + "<article>" + " ".join(["word"] * 250) + "</article>"
    raw += "</body></html>"
    mocker.patch.object(
        ArticleExtractor, "_fetch", return_value=(raw, "https://example.com/article")
    )
    result = await ArticleExtractor().extract("https://example.com/article")
    assert result.metadata["low_confidence"] is False


@pytest.mark.asyncio
async def test_article_extractor_fetch_rejects_blocked_host():
    """_fetch validates *url* before ever touching the network — see ssrf.py."""
    with pytest.raises(ExtractionError):
        await ArticleExtractor()._fetch("http://127.0.0.1/admin")


@pytest.mark.asyncio
async def test_article_extractor_fetch_succeeds_through_real_client_with_hook(mocker):
    """Regression guard: the redirect-blocking event_hooks wiring in _fetch must
    not break an ordinary, non-redirected fetch through the real AsyncClient
    (a sync hook there previously broke every response — see ssrf.py)."""
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(200, text="<html><body>ok</body></html>")
    )
    real_client = httpx2.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    mocker.patch(
        "analecta.extraction.article.httpx2.AsyncClient", side_effect=client_factory
    )
    html, final_url = await ArticleExtractor()._fetch("https://example.com/article")
    assert html == "<html><body>ok</body></html>"
    assert final_url == "https://example.com/article"


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


@pytest.mark.asyncio
async def test_substack_extractor_inbox_blocked_host_raises_before_head_request(
    mocker,
):
    """An inbox-shaped URL on a blocked host never fires the HEAD request."""
    mock_client_class = mocker.patch("analecta.extraction.social.httpx2.AsyncClient")

    with pytest.raises(ExtractionError):
        await SubstackExtractor().extract("http://127.0.0.1/inbox/post/99")

    mock_client_class.assert_not_called()


# ---------------------------------------------------------------------------
# XExtractor
#
# Fixtures below are real captured JSON from cdn.syndication.twimg.com (not
# synthetic), fetched during planning against currently-live public tweets,
# trimmed to only the fields XExtractor actually reads.
# ---------------------------------------------------------------------------

_TWEET_NASA_WALLOPS: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "875083037872181254",
    "text": (
        "UPDATE: 4pm 6/14:  NASA Terrier Improved Malemute will launch no earlier "
        "than 6/16, from 9:05 to 9:20 p.m. due to poor weather conditions. "
        "https://t.co/8LLQsJw5pM"
    ),
    "display_text_range": [0, 138],
    "created_at": "2017-06-14T20:10:29.000Z",
    "entities": {},
    "user": {
        "id_str": "30258963",
        "name": "NASA Wallops",
        "screen_name": "NASAWallops",
    },
    "photos": [{"url": "https://pbs.twimg.com/media/DCTrTynXgAEwyNJ.jpg"}],
    "mediaDetails": [
        {
            "type": "photo",
            "expanded_url": "https://x.com/NASA_Wallops/status/875083037872181254/photo/1",
            "media_url_https": "https://pbs.twimg.com/media/DCTrTynXgAEwyNJ.jpg",
        }
    ],
}

_TWEET_DUOLINGO: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "848097704869851136",
    "text": (
        "Announcing the world's first Emoji course! Behold, the final chapter in "
        "the evolution of human communication. ✏️🏋🚀: https://t.co/kGejjXiTGf "
        "https://t.co/okK12CbvdQ"
    ),
    "display_text_range": [0, 141],
    "created_at": "2017-04-01T09:00:24.000Z",
    "entities": {
        "urls": [
            {
                "indices": [116, 139],
                "expanded_url": "http://emoji.duolingo.com",
                "display_url": "emoji.duolingo.com",
                "url": "https://t.co/kGejjXiTGf",
            }
        ]
    },
    "user": {"id_str": "107238136", "name": "Duolingo", "screen_name": "duolingo"},
    "photos": [{"url": "https://pbs.twimg.com/media/C8UMXURXcAA1T3d.jpg"}],
    "mediaDetails": [
        {
            "type": "photo",
            "expanded_url": "https://x.com/duolingo/status/848097704869851136/photo/1",
            "media_url_https": "https://pbs.twimg.com/media/C8UMXURXcAA1T3d.jpg",
        }
    ],
}

_TWEET_ARTEMIS: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "1592721757294587905",
    "text": (
        "LIVE NOW: The #Artemis era of exploration begins today with @NASAArtemis "
        "I, the first integrated test flight of the rocket and spacecraft that "
        "will bring humanity to the Moon. Watch @NASA_SLS and @NASA_Orion embark "
        "on their first voyage. https://t.co/Ngak08VFb0"
    ),
    "display_text_range": [0, 261],
    "created_at": "2022-11-16T03:30:32.000Z",
    "entities": {
        "urls": [
            {
                "indices": [238, 261],
                "expanded_url": "https://x.com/i/broadcasts/1jMKgLaeYoAGL",
                "display_url": "x.com/i/broadcasts/1…",
                "url": "https://t.co/Ngak08VFb0",
            }
        ],
        "hashtags": [{"indices": [14, 22], "text": "Artemis"}],
    },
    "user": {"id_str": "11348282", "name": "NASA", "screen_name": "NASA"},
}

_TWEET_TOMBSTONE: dict[str, Any] = {
    "__typename": "TweetTombstone",
    "tombstone": {
        "text": {"text": "This Post was deleted by the Post author. Learn more"}
    },
}

_TWEET_MULTILINE: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "2077347509769269312",
    "text": ("Line one \n\nLine two \n\n Line three https://t.co/QLINib63jk"),
    "display_text_range": [0, 34],
    "created_at": "2026-07-15T11:00:16.000Z",
    "entities": {},
    "user": {"id_str": "1", "name": "Lunaticoin", "screen_name": "lunaticoin"},
}

_TWEET_SPACEX_REPLY: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "1574541890120081409",
    "text": (
        "@NASA Congratulations on successfully crashing a spacecraft into an asteroid!"
    ),
    "display_text_range": [6, 77],
    "created_at": "2022-09-26T23:30:14.000Z",
    "entities": {},
    "user": {"id_str": "34743251", "name": "SpaceX", "screen_name": "SpaceX"},
    "in_reply_to_status_id_str": "1574539270987173903",
}

_TWEET_NASA_IMPACT: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "1574539270987173903",
    "text": (
        "IMPACT SUCCESS! Watch from #DARTMIssion's DRACO Camera, as the vending "
        "machine-sized spacecraft successfully collides with asteroid Dimorphos, "
        "which is the size of a football stadium and poses no threat to Earth. "
        "https://t.co/7bXipPkjWD"
    ),
    "display_text_range": [0, 212],
    "created_at": "2022-09-26T23:19:50.000Z",
    "entities": {"hashtags": [{"indices": [27, 39], "text": "DARTMIssion"}]},
    "user": {"id_str": "11348282", "name": "NASA", "screen_name": "NASA"},
    "photos": [],
    "mediaDetails": [
        {
            "type": "video",
            "expanded_url": "https://x.com/NASA/status/1574539270987173903/video/1",
            "media_url_https": "https://pbs.twimg.com/media/Fdni7dwX0AgxyLv.jpg",
        }
    ],
}

# Synthetic 3-tweet chain, three distinct authors throughout (Alice -> Bob ->
# Charlie), used to prove _walk_reply_chain climbs through every author
# change to the true root instead of stopping at the first one.
_TWEET_CROSSAUTHOR_ROOT: dict[str, Any] = {
    "id_str": "9003",
    "text": "Original post from Alice",
    "display_text_range": [0, 25],
    "entities": {},
    "user": {"id_str": "1", "name": "Alice", "screen_name": "alice"},
}

_TWEET_CROSSAUTHOR_MIDDLE: dict[str, Any] = {
    "id_str": "9002",
    "text": "Reply from Bob",
    "display_text_range": [0, 15],
    "entities": {},
    "user": {"id_str": "2", "name": "Bob", "screen_name": "bob"},
    "in_reply_to_status_id_str": "9003",
}

_TWEET_CROSSAUTHOR_TAIL: dict[str, Any] = {
    "id_str": "9001",
    "text": "Reply from Charlie",
    "display_text_range": [0, 19],
    "entities": {},
    "user": {"id_str": "3", "name": "Charlie", "screen_name": "charlie"},
    "in_reply_to_status_id_str": "9002",
}

# Real Note Tweet (X's long-form composer, over the legacy ~280-char limit) —
# live-captured shape from x.com/Pontifex/status/2073354181797097723:
# syndication's text/display_text_range only carry the legacy-length preview,
# and note_tweet is just an opaque object reference, not the full body.
_TWEET_NOTE_TRUNCATED: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "2073354181797097723",
    "text": (
        "Defending human life also includes welcoming, protecting and assisting "
        "immigrants, whose hopes, sacrifices and contribution have formed part of "
        "the history of this country from its very beginning.  In every "
        "generation, those who have arrived seeking freedom, opportunity and a"
    ),
    "display_text_range": [0, 276],
    "created_at": "2026-07-04T10:32:11.000Z",
    "entities": {},
    "user": {"id_str": "1", "name": "Pope Leo XIV", "screen_name": "Pontifex"},
    "note_tweet": {"id": "Tm90ZVR3ZWV0UmVzdWx0czoyMDczMzU0MTgxNzMwMDA0OTky"},
}

# Real same-author 3-tweet thread (a numbered thread ending "/13", "/14",
# "/fin"), tail carries real animated_gif media.
_TWEET_GIF_ROOT: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "1003318785330241537",
    "text": (
        "Consent needs to be an ongoing conversation. It needs to start before "
        "you get naked, continue during sexy times, and honestly pillowtalk "
        "retros are pretty great too\n\n"
        "“But that's a lot of talking!”\n\n"
        "Yes it is! And it's totally worth it /13 https://t.co/mIv1dKmeYP"
    ),
    "display_text_range": [0, 238],
    "created_at": "2018-06-03T16:53:33.000Z",
    "entities": {},
    "user": {
        "id_str": "330100136",
        "name": "Danielle Leong",
        "screen_name": "tsunamino",
    },
    "photos": [],
    "mediaDetails": [
        {
            "type": "animated_gif",
            "expanded_url": "https://x.com/tsunamino/status/1003318785330241537/photo/1",
            "media_url_https": "https://pbs.twimg.com/tweet_video_thumb/DeyBHVAU0AAButJ.jpg",
        }
    ],
}

_TWEET_GIF_MIDDLE: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "1003318790405304321",
    "text": (
        "If you can't have a conversation about what you like and don't like "
        "with another person, sounds like you should work on that by yourself! "
        "Maybe journal it or talk to your therapist. /14"
    ),
    "display_text_range": [0, 185],
    "created_at": "2018-06-03T16:53:35.000Z",
    "entities": {},
    "user": {
        "id_str": "330100136",
        "name": "Danielle Leong",
        "screen_name": "tsunamino",
    },
    "in_reply_to_status_id_str": "1003318785330241537",
}

_TWEET_GIF_TAIL: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "1003318804619804672",
    "text": "Hope this helps! Go forth and have good (consensual) sex /fin https://t.co/XgIno4tSLG",
    "display_text_range": [0, 61],
    "created_at": "2018-06-03T16:53:38.000Z",
    "entities": {},
    "user": {
        "id_str": "330100136",
        "name": "Danielle Leong",
        "screen_name": "tsunamino",
    },
    "photos": [],
    "mediaDetails": [
        {
            "type": "animated_gif",
            "expanded_url": "https://x.com/tsunamino/status/1003318804619804672/photo/1",
            "media_url_https": "https://pbs.twimg.com/tweet_video_thumb/DeyBINOUwAAbuif.jpg",
        }
    ],
    "in_reply_to_status_id_str": "1003318790405304321",
}

_TWEET_QUOTE: dict[str, Any] = {
    "__typename": "Tweet",
    "id_str": "1562916200866267138",
    "text": (
        "Congresswoman Marjorie Taylor Greene had $183,504 in PPP loans "
        "forgiven.\n\nhttps://t.co/4FoCymt8TB"
    ),
    "display_text_range": [0, 97],
    "created_at": "2022-08-25T21:33:54.000Z",
    "entities": {
        "urls": [
            {
                "indices": [74, 97],
                "expanded_url": (
                    "https://x.com/Acyn/status/1562530929838436355"
                    "?s=20&t=Anxeqtkb5PiVIELnC7dCoA"
                ),
                "display_url": "x.com/Acyn/status/15…",
                "url": "https://t.co/4FoCymt8TB",
            }
        ]
    },
    "user": {
        "id_str": "1323730225067339784",
        "name": "The White House 46 Archived",
        "screen_name": "WhiteHouse46",
    },
    "quoted_tweet": {
        "id_str": "1562530929838436355",
        "text": (
            "Greene: For our government just to say ok your debt is completely "
            "forgiven.. it's completely unfair https://t.co/V0yJWYSbot"
        ),
        "display_text_range": [0, 99],
        "created_at": "2022-08-24T20:02:58.000Z",
        "entities": {},
        "user": {"id_str": "16635277", "name": "Acyn", "screen_name": "Acyn"},
        "photos": [],
        "mediaDetails": [
            {
                "type": "video",
                "expanded_url": "https://x.com/Acyn/status/1562530929838436355/video/1",
                "media_url_https": (
                    "https://pbs.twimg.com/ext_tw_video_thumb/1562530825316380673"
                    "/pu/img/6ARRpJDRacbuRi3P.jpg"
                ),
            }
        ],
    },
}


# --- _extract_tweet_id ------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/user/status/123", "123"),
        ("https://twitter.com/user/status/123", "123"),
        ("https://mobile.twitter.com/user/status/123", "123"),
        ("https://x.com/i/web/status/123", "123"),
        ("https://x.com/user/statuses/123", "123"),
        ("https://x.com/user/status/123?s=20", "123"),
        ("https://example.com/not-a-tweet", None),
        ("https://x.com/user", None),
    ],
)
def test_extract_tweet_id(url, expected):
    assert _extract_tweet_id(url) == expected


# --- _syndication_token ------------------------------------------------------


def test_syndication_token_real_id():
    """Pinned against a real 19-digit id, not id 20 (whose token comes
    entirely from the fractional branch and would stay green even if the
    integer-conversion branch were broken)."""
    assert _syndication_token("875083037872181254") == "24d5k5p19ji9z3qrg6p9cnmi"


# --- _fetch_syndication (HTTP layer) ------------------------------------------


@pytest.mark.asyncio
async def test_fetch_syndication_treats_tombstone_as_unavailable(mocker):
    """A tombstone (deleted/protected tweet) is a real 200 response without a
    "text" field -- must not be mistaken for a successful fetch."""
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _TWEET_TOMBSTONE
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    assert await _fetch_syndication("1629307668568633344") is None


@pytest.mark.asyncio
async def test_fetch_syndication_success_returns_tweet_dict(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _TWEET_NASA_WALLOPS
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    result = await _fetch_syndication("875083037872181254")
    assert result == _TWEET_NASA_WALLOPS


@pytest.mark.asyncio
async def test_fetch_syndication_non_200_returns_none(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 404
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    assert await _fetch_syndication("1") is None


@pytest.mark.asyncio
async def test_fetch_syndication_network_error_returns_none(mocker):
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(
        side_effect=Exception("network error")
    )
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    assert await _fetch_syndication("1") is None


# --- _fetch_oembed (HTTP layer) -----------------------------------------------


@pytest.mark.asyncio
async def test_fetch_oembed_success_returns_dict(mocker):
    oembed_data = {"author_name": "Some Author", "html": "<blockquote></blockquote>"}
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = oembed_data
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    result = await _fetch_oembed("https://x.com/user/status/1")
    assert result == oembed_data


@pytest.mark.asyncio
async def test_fetch_oembed_non_200_returns_none(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 404
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    assert await _fetch_oembed("https://x.com/user/status/1") is None


# --- _utf16_offset_to_codepoint -----------------------------------------------


def test_utf16_offset_to_codepoint_ascii_identity():
    assert _utf16_offset_to_codepoint("hello", 3) == 3


def test_utf16_offset_to_codepoint_astral_divergence():
    """Two astral-plane emoji (each 2 UTF-16 units, 1 Python codepoint) before
    the cut point make the UTF-16 offset and the codepoint offset diverge by
    exactly 2 -- live-verified against the real Duolingo tweet during
    planning."""
    text = _TWEET_DUOLINGO["text"]
    assert _utf16_offset_to_codepoint(text, 141) == 139


# --- _clean_tweet_text / _display_text ---------------------------------------


def test_display_text_reply_skips_leading_mention():
    assert _display_text(_TWEET_SPACEX_REPLY).startswith("Congratulations")


def test_clean_tweet_text_trims_trailing_media_link():
    cleaned = _clean_tweet_text(_TWEET_NASA_WALLOPS)
    assert "t.co" not in cleaned
    assert cleaned.startswith("UPDATE: 4pm 6/14:")
    assert cleaned.endswith("poor weather conditions.")


def test_clean_tweet_text_handles_utf16_boundary_and_link_expansion():
    """Regression guard for the UTF-16-vs-codepoint mixed-index-space finding:
    astral emoji before both the cut point and an in-text link."""
    cleaned = _clean_tweet_text(_TWEET_DUOLINGO)
    assert "🚀" in cleaned
    assert "t.co" not in cleaned
    assert '<a href="http://emoji.duolingo.com">emoji.duolingo.com</a>' in cleaned
    assert cleaned.endswith(
        '<a href="http://emoji.duolingo.com">emoji.duolingo.com</a>'
    )


def test_clean_tweet_text_linkifies_hashtag_and_leaves_no_backlink():
    from analecta.markdown.converter import MarkdownConverter

    cleaned = _clean_tweet_text(_TWEET_ARTEMIS)
    assert '<a href="https://x.com/hashtag/Artemis">#Artemis</a>' in cleaned

    markdown = MarkdownConverter()._html_to_md(f"<p>{cleaned}</p>")
    refs = parse_refs(markdown)
    assert not any(r.is_hashtag for r in refs)


def test_clean_tweet_text_reply_has_no_leading_mention_noise():
    """Regression guard for the display_text_range[0] fix: every fixture used
    elsewhere is a non-reply tweet with start=0, so this is the only case
    that exercises a non-zero start."""
    cleaned = _clean_tweet_text(_TWEET_SPACEX_REPLY)
    assert not cleaned.startswith("@NASA")
    assert cleaned.startswith("Congratulations")


def test_clean_tweet_text_converts_newlines_to_br():
    """Regression guard: X's ``text`` carries the author's real line breaks
    verbatim (live-verified against x.com/lunaticoin/status/2077347509769269312).
    Left as plain ``\\n``, markdownify collapses them into a Markdown soft
    break, which renders as a space rather than a line break."""
    cleaned = _clean_tweet_text(_TWEET_MULTILINE)
    assert "\n" not in cleaned
    assert cleaned == "Line one <br><br>Line two <br><br> Line three "


def test_clean_tweet_text_newlines_survive_as_hard_breaks_in_markdown():
    """End-to-end: the <br> from _clean_tweet_text must reach markdownify as
    a real hard break (trailing double-space), not collapse away, so the
    final rendered Markdown still shows the line break regardless of the
    renderer's soft-break setting."""
    from analecta.markdown.converter import MarkdownConverter

    rendered = _render_tweet_html(_TWEET_MULTILINE)
    markdown = MarkdownConverter()._html_to_md(rendered)
    assert "  \n" in markdown
    assert "Line one" in markdown
    assert "Line two" in markdown
    assert "Line three" in markdown


# --- _render_tweet_html -------------------------------------------------------


def test_render_tweet_html_photo_becomes_img():
    rendered = _render_tweet_html(_TWEET_NASA_WALLOPS)
    assert (
        '<img src="https://pbs.twimg.com/media/DCTrTynXgAEwyNJ.jpg" alt="">' in rendered
    )


def test_render_tweet_html_video_is_link_out_not_download():
    rendered = _render_tweet_html(_TWEET_NASA_IMPACT)
    assert "<img" not in rendered
    assert (
        '<a href="https://x.com/NASA/status/1574539270987173903/video/1">' in rendered
    )


def test_render_tweet_html_animated_gif_is_link_out_not_download():
    rendered = _render_tweet_html(_TWEET_GIF_TAIL)
    assert "<img" not in rendered
    assert (
        '<a href="https://x.com/tsunamino/status/1003318804619804672/photo/1">'
        in rendered
    )


def test_render_tweet_html_quoted_tweet_nested_blockquote():
    rendered = _render_tweet_html(_TWEET_QUOTE)
    assert "<blockquote>" in rendered
    assert "Greene" in rendered
    assert (
        '<a href="https://x.com/Acyn/status/1562530929838436355/video/1">' in rendered
    )


def test_render_tweet_html_starts_with_author_line():
    rendered = _render_tweet_html(_TWEET_NASA_WALLOPS)
    assert rendered.startswith(
        '<p><strong><a href="https://x.com/NASAWallops">NASA Wallops '
        "(@NASAWallops)</a></strong><br>"
    )


def test_render_tweet_html_author_line_and_text_share_one_paragraph():
    """Regression guard: author line and tweet text must share a single
    ``<p>`` (joined by ``<br>``), not two separate paragraphs — otherwise
    markdownify renders a blank line between the byline and the text."""
    rendered = _render_tweet_html(_TWEET_NASA_WALLOPS)
    assert "</strong></p><p>" not in rendered
    assert "</strong><br>" in rendered


def test_render_tweet_html_quoted_tweet_has_own_nested_author_line():
    """Both the outer tweet and its quote get their own attribution, so a
    reader can tell the two authors apart without leaving the blockquote."""
    rendered = _render_tweet_html(_TWEET_QUOTE)
    assert (
        '<a href="https://x.com/WhiteHouse46">The White House 46 Archived '
        "(@WhiteHouse46)</a>" in rendered
    )
    assert '<a href="https://x.com/Acyn">Acyn (@Acyn)</a>' in rendered


def test_render_tweet_html_note_tweet_gets_visible_truncation_marker():
    """Regression guard for the 2026-07-23 truncation finding: a Note Tweet's
    text is silently cut mid-sentence by the syndication endpoint itself
    (not something Analecta does) — the fix is to make that visible, not
    to chase the full text via a fragile undocumented scrape."""
    rendered = _render_tweet_html(_TWEET_NOTE_TRUNCATED)
    assert "Tweet truncated" in rendered
    assert "long-form tweets" in rendered


def test_render_tweet_html_normal_tweet_has_no_truncation_marker():
    rendered = _render_tweet_html(_TWEET_NASA_WALLOPS)
    assert "Tweet truncated" not in rendered


# --- _author_line_html --------------------------------------------------------


def test_author_line_html_bold_and_linked_to_profile():
    assert _author_line_html(_TWEET_NASA_WALLOPS) == (
        '<strong><a href="https://x.com/NASAWallops">NASA Wallops '
        "(@NASAWallops)</a></strong>"
    )


def test_author_line_html_no_screen_name_omits_link():
    assert _author_line_html({"user": {"name": "Solo Name"}}) == (
        "<strong>Solo Name</strong>"
    )


def test_author_line_html_no_user_returns_empty_string():
    assert _author_line_html({}) == ""


# --- _walk_reply_chain ---------------------------------------------------------


@pytest.mark.asyncio
async def test_walk_reply_chain_stops_at_root():
    chain, complete = await _walk_reply_chain(_TWEET_NASA_WALLOPS)
    assert chain == [_TWEET_NASA_WALLOPS]
    assert complete is True


@pytest.mark.asyncio
async def test_walk_reply_chain_includes_cross_author_parent(mocker):
    """A cross-author parent is included same as a same-author one; this walk
    stops because ``_TWEET_NASA_IMPACT`` has no further parent (genuine
    root), not because its author differs from the SpaceX reply's — see
    ``test_walk_reply_chain_continues_past_cross_author_tweet`` for a chain
    that keeps climbing through multiple author changes."""
    mock_fetch = mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(return_value=_TWEET_NASA_IMPACT),
    )
    chain, complete = await _walk_reply_chain(_TWEET_SPACEX_REPLY)
    assert [t["id_str"] for t in chain] == [
        _TWEET_NASA_IMPACT["id_str"],
        _TWEET_SPACEX_REPLY["id_str"],
    ]
    assert complete is True
    mock_fetch.assert_called_once_with("1574539270987173903")


@pytest.mark.asyncio
async def test_walk_reply_chain_continues_past_cross_author_tweet(mocker):
    """Regression guard for the 2026-07-23 ceiling removal: the walk used to
    stop at the first author change, including exactly one cross-author
    tweet. It must now keep climbing through as many author changes as the
    chain actually has, all the way to the true root."""
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(
            side_effect=[_TWEET_CROSSAUTHOR_MIDDLE, _TWEET_CROSSAUTHOR_ROOT]
        ),
    )
    chain, complete = await _walk_reply_chain(_TWEET_CROSSAUTHOR_TAIL)
    assert [t["id_str"] for t in chain] == [
        _TWEET_CROSSAUTHOR_ROOT["id_str"],
        _TWEET_CROSSAUTHOR_MIDDLE["id_str"],
        _TWEET_CROSSAUTHOR_TAIL["id_str"],
    ]
    assert complete is True


@pytest.mark.asyncio
async def test_walk_reply_chain_full_same_author_thread(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(side_effect=[_TWEET_GIF_MIDDLE, _TWEET_GIF_ROOT]),
    )
    chain, complete = await _walk_reply_chain(_TWEET_GIF_TAIL)
    assert [t["id_str"] for t in chain] == [
        _TWEET_GIF_ROOT["id_str"],
        _TWEET_GIF_MIDDLE["id_str"],
        _TWEET_GIF_TAIL["id_str"],
    ]
    assert complete is True


@pytest.mark.asyncio
async def test_walk_reply_chain_ambiguous_failure_marks_incomplete(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(return_value=None),
    )
    chain, complete = await _walk_reply_chain(_TWEET_GIF_TAIL)
    assert chain == [_TWEET_GIF_TAIL]
    assert complete is False


@pytest.mark.asyncio
async def test_walk_reply_chain_max_hops_cap_marks_incomplete(mocker):
    looping_tweet = {**_TWEET_GIF_MIDDLE}  # always same author, always has a parent
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(return_value=looping_tweet),
    )
    chain, complete = await _walk_reply_chain(_TWEET_GIF_TAIL, max_hops=3)
    assert len(chain) == 4  # tail + 3 capped hops
    assert complete is False


# --- small helpers / edge cases -------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_syndication_malformed_json_returns_none(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("not json")
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    assert await _fetch_syndication("1") is None


@pytest.mark.asyncio
async def test_fetch_oembed_malformed_json_returns_none(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("not json")
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    assert await _fetch_oembed("https://x.com/user/status/1") is None


@pytest.mark.asyncio
async def test_fetch_oembed_missing_html_key_returns_none(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"author_name": "Someone"}
    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value.get = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch("analecta.extraction.x.httpx2.AsyncClient", return_value=mock_client)

    assert await _fetch_oembed("https://x.com/user/status/1") is None


def test_render_tweet_html_video_without_expanded_url_falls_back_to_permalink():
    tweet = {
        **_TWEET_NASA_WALLOPS,
        "id_str": "999",
        "user": {"screen_name": "someone"},
        "mediaDetails": [{"type": "video"}],
    }
    rendered = _render_tweet_html(tweet)
    assert '<a href="https://x.com/someone/status/999">' in rendered


def test_render_tweet_html_skips_photo_without_url():
    tweet = {**_TWEET_NASA_WALLOPS, "photos": [{}]}
    rendered = _render_tweet_html(tweet)
    assert "<img" not in rendered


def test_display_text_no_range_returns_raw_text():
    assert _display_text({"text": "no range here"}) == "no range here"


def test_format_author_missing_name_falls_back_to_screen_name():
    from analecta.extraction.x import _format_author

    assert _format_author({"user": {"screen_name": "onlyhandle"}}) == "onlyhandle"
    assert _format_author({"user": {}}) == ""


def test_build_title_empty_text_uses_fallback():
    from analecta.extraction.x import _build_title

    assert _build_title("   ", "fallback title") == "fallback title"


def test_oembed_paragraphs_falls_back_to_plain_text_without_blockquote():
    from analecta.extraction.x import _oembed_paragraphs

    assert _oembed_paragraphs({"html": "<p>just a paragraph</p>"}) == [
        "just a paragraph"
    ]
    assert _oembed_paragraphs({"html": ""}) == []


# --- XExtractor.extract (end-to-end) ------------------------------------------


@pytest.mark.asyncio
async def test_x_extractor_syndication_success(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(return_value=_TWEET_NASA_WALLOPS),
    )
    result = await XExtractor().extract(
        "https://x.com/NASA_Wallops/status/875083037872181254"
    )
    assert result.source_type == "x"
    assert result.metadata["fetch_method"] == "syndication"
    assert "thread_length" not in result.metadata
    assert result.metadata["author"] == "NASA Wallops (@NASAWallops)"
    assert '<img src="https://pbs.twimg.com/media/DCTrTynXgAEwyNJ.jpg"' in result.html


@pytest.mark.asyncio
async def test_x_extractor_thread_walk_end_to_end(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(
            side_effect=[_TWEET_GIF_TAIL, _TWEET_GIF_MIDDLE, _TWEET_GIF_ROOT]
        ),
    )
    result = await XExtractor().extract(
        "https://x.com/tsunamino/status/1003318804619804672"
    )
    assert result.metadata["thread_length"] == 3
    assert result.metadata["author"] == "Danielle Leong (@tsunamino)"
    assert "Thread may be incomplete" not in result.html
    assert (
        "/13" in result.html
        or "Consent needs to be an ongoing conversation" in result.html
    )
    assert "/fin" in result.html
    assert result.html.count("<hr>") == 2


@pytest.mark.asyncio
async def test_x_extractor_single_tweet_has_no_separator(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(return_value=_TWEET_NASA_WALLOPS),
    )
    result = await XExtractor().extract(
        "https://x.com/NASA_Wallops/status/875083037872181254"
    )
    assert "<hr>" not in result.html


@pytest.mark.asyncio
async def test_x_extractor_incomplete_thread_marker(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(side_effect=[_TWEET_GIF_TAIL, None]),
    )
    result = await XExtractor().extract(
        "https://x.com/tsunamino/status/1003318804619804672"
    )
    assert "Thread may be incomplete" in result.html


@pytest.mark.asyncio
async def test_x_extractor_falls_back_to_oembed_on_tombstone(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(return_value=None),
    )
    mocker.patch(
        "analecta.extraction.x._fetch_oembed",
        new=mocker.AsyncMock(
            return_value={
                "author_name": "Some Author",
                "html": (
                    '<blockquote class="twitter-tweet">'
                    "<p>Fallback tweet text</p>"
                    "&mdash; Some Author (@someauthor)"
                    "</blockquote>"
                ),
            }
        ),
    )
    result = await XExtractor().extract("https://x.com/user/status/1629307668568633344")
    assert result.metadata["fetch_method"] == "oembed_fallback"
    assert "Fetched via oEmbed fallback" in result.html
    assert "Fallback tweet text" in result.html
    assert result.metadata["author"] == "Some Author"


@pytest.mark.asyncio
async def test_x_extractor_raises_when_both_paths_fail(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(return_value=None),
    )
    mocker.patch(
        "analecta.extraction.x._fetch_oembed",
        new=mocker.AsyncMock(return_value=None),
    )
    with pytest.raises(ExtractionError, match="123"):
        await XExtractor().extract("https://x.com/user/status/123")


@pytest.mark.asyncio
async def test_x_extractor_unparseable_url_raises_without_network(mocker):
    mock_fetch = mocker.patch("analecta.extraction.x._fetch_syndication")
    with pytest.raises(ExtractionError):
        await XExtractor().extract("https://x.com/user")
    mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# tweet_embeds: resolving classic Twitter/X widget embeds inside article HTML
# ---------------------------------------------------------------------------

_BLOCKQUOTE_NASA_WALLOPS = (
    '<blockquote class="twitter-tweet"><p lang="en" dir="ltr">'
    "UPDATE: 4pm 6/14: NASA Terrier Improved Malemute will launch no earlier "
    "than 6/16, from 9:05 to 9:20 p.m. due to poor weather conditions. "
    '<a href="https://t.co/8LLQsJw5pM">https://t.co/8LLQsJw5pM</a></p>'
    "&mdash; NASA Wallops (@NASAWallops) "
    '<a href="https://twitter.com/NASAWallops/status/875083037872181254'
    '?ref_src=twsrc%5Etfw">June 14, 2017</a>'
    "</blockquote>"
)

_ARTICLE_WITH_BLOCKQUOTE_EMBED = f"""
<article>
<h1>Some Article</h1>
<p>Real prose before the embed, several sentences long so a realistic
extraction scoring pass has enough surrounding text to work with.</p>
{_BLOCKQUOTE_NASA_WALLOPS}
<p>Real prose after the embed, continuing the discussion at similar length.</p>
</article>
"""

_IFRAME_EMBED = (
    '<iframe src="https://platform.x.com/embed/Tweet.html'
    '?id=875083037872181254&theme=light"></iframe>'
)


def test_iframe_tweet_id_matches_twitter_and_x_hosts():
    assert (
        _iframe_tweet_id("https://platform.twitter.com/embed/Tweet.html?id=123")
        == "123"
    )
    assert (
        _iframe_tweet_id("https://platform.x.com/embed/Tweet.html?id=456&theme=dark")
        == "456"
    )


def test_iframe_tweet_id_none_for_unrelated_host():
    assert _iframe_tweet_id("https://www.youtube.com/embed/abc") is None
    assert _iframe_tweet_id("https://platform.x.com/widgets.js") is None
    assert _iframe_tweet_id("") is None


def test_permalink_from_blockquote_finds_status_link():
    bq = BeautifulSoup(_BLOCKQUOTE_NASA_WALLOPS, "html.parser").find("blockquote")
    assert isinstance(bq, Tag)
    permalink = _permalink_from_blockquote(bq)
    assert permalink is not None
    assert "875083037872181254" in permalink


def test_permalink_from_blockquote_none_without_status_link():
    bq = BeautifulSoup(
        '<blockquote class="twitter-tweet"><p>no links here</p></blockquote>',
        "html.parser",
    ).find("blockquote")
    assert isinstance(bq, Tag)
    assert _permalink_from_blockquote(bq) is None


def test_reshape_fallback_blockquote_escapes_text_and_links_permalink():
    bq = BeautifulSoup(_BLOCKQUOTE_NASA_WALLOPS, "html.parser").find("blockquote")
    assert isinstance(bq, Tag)
    reshaped = _reshape_fallback_blockquote(bq)
    assert "NASA Terrier Improved Malemute" in reshaped
    assert 'href="https://twitter.com/NASAWallops/status/875083037872181254' in reshaped
    assert "View tweet on X" in reshaped
    assert "twitter-tweet" not in reshaped


def test_iframe_fallback_link_builds_i_web_status_url():
    link = _iframe_fallback_link("875083037872181254")
    assert link == (
        '<p><a href="https://x.com/i/web/status/875083037872181254">'
        "View tweet on X</a></p>"
    )


@pytest.mark.asyncio
async def test_resolve_embedded_tweets_noop_without_embeds(mocker):
    mock_fetch = mocker.patch("analecta.extraction.tweet_embeds._fetch_syndication")
    html = "<article><p>no embeds here</p></article>"
    assert await resolve_embedded_tweets(html) == html
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_embedded_tweets_blockquote_success_renders_author_text_and_photo(
    mocker,
):
    mocker.patch(
        "analecta.extraction.tweet_embeds._fetch_syndication",
        new=mocker.AsyncMock(return_value=_TWEET_NASA_WALLOPS),
    )
    out = await resolve_embedded_tweets(_ARTICLE_WITH_BLOCKQUOTE_EMBED)
    assert "NASA Wallops" in out
    assert "NASA Terrier Improved Malemute" in out
    assert "DCTrTynXgAEwyNJ.jpg" in out
    assert "twitter-tweet" not in out


@pytest.mark.asyncio
async def test_resolve_embedded_tweets_blockquote_failure_falls_back_to_reshaped_text(
    mocker,
):
    mocker.patch(
        "analecta.extraction.tweet_embeds._fetch_syndication",
        new=mocker.AsyncMock(return_value=None),
    )
    out = await resolve_embedded_tweets(_ARTICLE_WITH_BLOCKQUOTE_EMBED)
    assert "NASA Terrier Improved Malemute" in out
    assert "View tweet on X" in out
    assert "twitter-tweet" not in out


@pytest.mark.asyncio
async def test_resolve_embedded_tweets_iframe_success_renders_tweet(mocker):
    mocker.patch(
        "analecta.extraction.tweet_embeds._fetch_syndication",
        new=mocker.AsyncMock(return_value=_TWEET_NASA_WALLOPS),
    )
    out = await resolve_embedded_tweets(f"<article>{_IFRAME_EMBED}</article>")
    assert "NASA Wallops" in out
    assert "<iframe" not in out


@pytest.mark.asyncio
async def test_resolve_embedded_tweets_iframe_failure_falls_back_to_permalink_only(
    mocker,
):
    mocker.patch(
        "analecta.extraction.tweet_embeds._fetch_syndication",
        new=mocker.AsyncMock(return_value=None),
    )
    out = await resolve_embedded_tweets(f"<article>{_IFRAME_EMBED}</article>")
    assert "https://x.com/i/web/status/875083037872181254" in out
    assert "<iframe" not in out


@pytest.mark.asyncio
async def test_resolve_embedded_tweets_malformed_blockquote_without_id_is_noop(mocker):
    """A twitter-tweet blockquote with no parseable status link (malformed
    markup) yields zero targets — must not crash, must not fetch, must
    return the html unchanged."""
    mock_fetch = mocker.patch("analecta.extraction.tweet_embeds._fetch_syndication")
    html = (
        '<article><blockquote class="twitter-tweet">'
        "<p>no status link</p></blockquote></article>"
    )
    assert await resolve_embedded_tweets(html) == html
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_embedded_tweets_ignores_unrelated_iframe(mocker):
    mock_fetch = mocker.patch("analecta.extraction.tweet_embeds._fetch_syndication")
    html = (
        '<article><iframe src="https://www.youtube.com/embed/abc"></iframe></article>'
    )
    assert await resolve_embedded_tweets(html) == html
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_embedded_tweets_multiple_embeds_preserve_order(mocker):
    second_blockquote = _BLOCKQUOTE_NASA_WALLOPS.replace(
        "875083037872181254", "848097704869851136"
    )
    html = f"""
<article>
{_BLOCKQUOTE_NASA_WALLOPS}
<p>separator</p>
{second_blockquote}
</article>
"""

    async def fake_fetch(tweet_id):
        return (
            _TWEET_NASA_WALLOPS if tweet_id == "875083037872181254" else _TWEET_DUOLINGO
        )

    mocker.patch(
        "analecta.extraction.tweet_embeds._fetch_syndication",
        new=mocker.AsyncMock(side_effect=fake_fetch),
    )
    out = await resolve_embedded_tweets(html)
    first_pos = out.find("NASA Wallops")
    separator_pos = out.find("separator")
    second_pos = out.find(_TWEET_DUOLINGO["user"]["name"])
    assert -1 < first_pos < separator_pos < second_pos


@pytest.mark.asyncio
async def test_article_extractor_resolves_blockquote_embed_end_to_end(mocker):
    article_html = f"""
<html><body><article>
<h1>Some Article Title</h1>
<p>This is the first paragraph of a real article with enough substantive
prose to make readability treat this as the main content candidate,
several sentences long, discussing the topic in depth so it scores well
above the noise threshold readability applies when picking the article
body over navigation or boilerplate.</p>
<p>Here is a second paragraph continuing the discussion with more detail
about the topic, again long enough to contribute meaningfully to the
text density score readability computes for this container element.</p>
{_BLOCKQUOTE_NASA_WALLOPS}
<p>And here is the paragraph that follows the embedded tweet, continuing
the article's argument with further explanation and additional sentences
to keep the surrounding container's text-to-link ratio high.</p>
</article></body></html>
"""
    mocker.patch.object(
        ArticleExtractor,
        "_fetch",
        return_value=(article_html, "https://example.com/article"),
    )
    mocker.patch(
        "analecta.extraction.tweet_embeds._fetch_syndication",
        new=mocker.AsyncMock(return_value=_TWEET_NASA_WALLOPS),
    )

    result = await ArticleExtractor().extract("https://example.com/article")

    assert "NASA Wallops" in result.html
    assert "NASA Terrier Improved Malemute" in result.html
    assert "twitter-tweet" not in result.html


def test_resolved_tweet_embed_survives_real_parse():
    """Regression guard: locks in the empirical finding that a *resolved*
    embed (this module's replacement shape) survives real readability/
    trafilatura scoring, unlike the raw oEmbed blockquote it replaces (which
    readability drops outright and trafilatura sometimes corrupts) — see
    module docstring in ``tweet_embeds.py``."""
    resolved_embed = (
        "<blockquote><p><strong>"
        '<a href="https://x.com/NASAWallops">NASA Wallops (@NASAWallops)</a>'
        "</strong><br>NASA Terrier Improved Malemute will launch no earlier "
        "than 6/16.</p></blockquote>"
    )
    article_html = f"""
<html><body><article>
<h1>Some Article Title</h1>
<p>This is the first paragraph of a real article with enough substantive
prose to make readability treat this as the main content candidate,
several sentences long, discussing the topic in depth so it scores well
above the noise threshold readability applies when picking the article
body over navigation or boilerplate.</p>
<p>Here is a second paragraph continuing the discussion with more detail
about the topic, again long enough to contribute meaningfully to the
text density score readability computes for this container element.</p>
{resolved_embed}
<p>And here is the paragraph that follows the embedded tweet, continuing
the article's argument with further explanation and additional sentences
to keep the surrounding container's text-to-link ratio high.</p>
</article></body></html>
"""
    result = ArticleExtractor()._parse(article_html, "https://example.com/article")
    assert "NASA Terrier Improved Malemute" in result.html
    assert "NASA Wallops" in result.html


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
async def test_extract_dispatches_x(mocker):
    mocker.patch(
        "analecta.extraction.x._fetch_syndication",
        new=mocker.AsyncMock(return_value=_TWEET_NASA_WALLOPS),
    )
    result = await extract("https://x.com/NASA_Wallops/status/875083037872181254")
    assert result.source_type == "x"


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


def test_rescue_linked_tables_link_with_br_survives_markdown_conversion():
    # Real-world MDN "Specifications" shape: the cell's <a> wraps a spec title,
    # a <br>, and a "# fragment" label -- reproduces the bug where flattening
    # the <td> into a bare <p> strips the table-cell context markdownify needs
    # to suppress the <br> to a space, producing a hard break that later broke
    # the link (a second line starting with "#" parsed as an ATX heading).
    from analecta.markdown.converter import MarkdownConverter

    html = (
        '<figure class="table-container"><table>'
        "<thead><tr><th>Specification</th></tr></thead>"
        "<tbody><tr><td>"
        '<a href="https://drafts.csswg.org/css-cascade-5/#css-inheritance">'
        "CSS Cascading and Inheritance Level 5<br># css-inheritance</a>"
        "</td></tr></tbody>"
        "</table></figure>"
    )
    rescued = _rescue_linked_tables(html)
    content = ExtractedContent(
        title="Inheritance",
        html=rescued,
        url="https://developer.mozilla.org/en-US/docs/Web/CSS/Inheritance",
        source_type="article",
    )
    md = MarkdownConverter().convert(content, "2024-01-15T10:00:00")
    assert (
        "[CSS Cascading and Inheritance Level 5 # css-inheritance]"
        "(https://drafts.csswg.org/css-cascade-5/#css-inheritance)" in md
    )
    assert "\n# css-inheritance" not in md


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
# _rescue_short_nested_lists
# ---------------------------------------------------------------------------


def test_rescue_short_nested_lists_inlines_single_item_list():
    html = (
        "<ul><li>Execution telemetry path:"
        "<ul><li><code>/api/github-heartbeat</code></li></ul>"
        "</li></ul>"
    )
    result = _rescue_short_nested_lists(html)
    soup = _BS(result, "html.parser")
    li = soup.find("li")
    assert li.find("ul") is None, "nested list should be dissolved"
    assert li.find("code").get_text() == "/api/github-heartbeat"
    assert "Execution telemetry path:" in li.get_text()


def test_rescue_short_nested_lists_collapses_pretty_printed_whitespace():
    # A pretty-printed/indented source (real-world HTML, not minified) adds
    # newlines and indentation as literal text nodes around the nested <li>.
    # A raw len(get_text()) would count that indentation and could push the
    # measured length past the rescue threshold even though the actual
    # content — and readability's own whitespace-collapsed text_length() —
    # is well under it. The rescue must still fire.
    html = (
        "<ul>\n"
        "  <li>Execution telemetry path:\n"
        "    <ul>\n"
        "      <li><code>/api/github-heartbeat</code></li>\n"
        "    </ul>\n"
        "  </li>\n"
        "</ul>\n"
    )
    result = _rescue_short_nested_lists(html)
    soup = _BS(result, "html.parser")
    li = soup.find("li")
    assert li.find("ul") is None, "nested list should be dissolved"
    assert li.find("code").get_text() == "/api/github-heartbeat"


def test_rescue_short_nested_lists_keeps_long_list():
    html = (
        "<ul><li>Payload delivery paths:"
        "<ul><li><code>/api/dl/386</code></li>"
        "<li><code>/api/dl/amd64</code></li>"
        "<li><code>/api/dl/arm</code></li>"
        "<li><code>/api/dl/arm64</code></li></ul>"
        "</li></ul>"
    )
    result = _rescue_short_nested_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("li").find("ul") is not None, (
        "long enough nested list must survive untouched"
    )


def test_rescue_short_nested_lists_joins_multiple_short_items_with_comma():
    html = "<ul><li>Ports:<ul><li>80</li><li>443</li></ul></li></ul>"
    result = _rescue_short_nested_lists(html)
    soup = _BS(result, "html.parser")
    li = soup.find("li")
    assert li.find("ul") is None
    assert "80, 443" in li.get_text()


def test_rescue_short_nested_lists_skips_nav_context():
    html = "<nav><ul><li>Docs:<ul><li>Guide</li></ul></li></ul></nav>"
    result = _rescue_short_nested_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("li").find("ul") is not None


def test_rescue_short_nested_lists_skips_negative_weight_list():
    # class="sidebar" -> readability's own negativeRe -> weight < 0. That's a
    # different removal signal than the length rule this function targets;
    # rescuing it would override readability's intent for an unrelated reason.
    html = '<ul><li>Links:<ul class="sidebar"><li>x</li></ul></li></ul>'
    result = _rescue_short_nested_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("li").find("ul") is not None


def test_rescue_short_nested_lists_skips_list_with_image():
    html = '<ul><li>Icon:<ul><li><img src="/x.png"></li></ul></li></ul>'
    result = _rescue_short_nested_lists(html)
    soup = _BS(result, "html.parser")
    assert soup.find("li").find("ul") is not None


def test_rescue_short_nested_lists_noop_without_nested_list():
    html = "<ul><li>Just plain text</li></ul>"
    result = _rescue_short_nested_lists(html)
    assert "Just plain text" in result


def test_rescue_short_nested_lists_survives_readability_min_text_length():
    # Regression test for the real socket.dev bug: a single short API path
    # in its own nested <ul> falls under readability's min_text_length (25
    # chars, default) and is dropped, even though the sibling 4-item list
    # (long enough) survives untouched.
    from readability import Document

    html = (
        "<html><body><article>"
        "<p>Padding paragraph one to give the surrounding article enough "
        "weight for readability to pick it as the main candidate region.</p>"
        "<ul>"
        "<li>Payload delivery paths:"
        "<ul><li><code>/api/dl/386</code></li>"
        "<li><code>/api/dl/amd64</code></li>"
        "<li><code>/api/dl/arm</code></li>"
        "<li><code>/api/dl/arm64</code></li></ul>"
        "</li>"
        "<li>Execution telemetry path:"
        "<ul><li><code>/api/github-heartbeat</code></li></ul>"
        "</li>"
        "</ul>"
        "<p>Padding paragraph two, also long enough to keep this region "
        "scored as the main content candidate for readability's algorithm.</p>"
        "</article></body></html>"
    )

    without_fix = Document(html).summary() or ""
    assert "github-heartbeat" not in without_fix, (
        "fixture doesn't reproduce the readability drop — adjust padding"
    )
    assert "/api/dl/386" in without_fix, (
        "the long sibling list should survive even without the fix"
    )

    fixed_html = _rescue_short_nested_lists(html)
    with_fix = Document(fixed_html).summary() or ""
    assert "github-heartbeat" in with_fix
    assert "/api/dl/386" in with_fix


# ---------------------------------------------------------------------------
# _rescue_short_figure_labels
# ---------------------------------------------------------------------------


def test_rescue_short_figure_labels_unwraps_label_before_figure():
    html = (
        '<div class="prose"><p><strong>See-through</strong></p></div>'
        '<figure><img src="x.png"><figcaption>caption</figcaption></figure>'
    )
    result = _rescue_short_figure_labels(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div") is None, "wrapper div should be unwrapped"
    p = soup.find("p")
    assert p is not None
    assert p.get_text() == "See-through"
    assert p.find_next_sibling("figure") is not None


def test_rescue_short_figure_labels_keeps_div_grouped_with_intro():
    # The div's own text is well over the threshold once grouped with a
    # heading/intro paragraph (the real system76 shape for the *first*
    # label in each section) -- readability never drops this one, so
    # there's nothing to rescue; unwrapping it anyway would be a no-op at
    # best and a needless DOM change at worst.
    html = (
        "<div><h3>Frosted Glass</h3>"
        "<p>Frosted Glass creates a more organic and immersive desktop "
        "experience, customizable in Settings.</p>"
        "<p><strong>Subtle and balanced</strong></p></div>"
        '<figure><img src="x.png"></figure>'
    )
    result = _rescue_short_figure_labels(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div") is not None, (
        "div with more than one child must be left untouched"
    )


def test_rescue_short_figure_labels_skips_when_not_followed_by_figure():
    html = "<div><p><strong>Short label</strong></p></div><p>Not a figure.</p>"
    result = _rescue_short_figure_labels(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div") is not None


def test_rescue_short_figure_labels_skips_long_label():
    html = (
        "<div><p><strong>A much longer caption label that clears the "
        "twenty-five character content threshold easily</strong></p></div>"
        '<figure><img src="x.png"></figure>'
    )
    result = _rescue_short_figure_labels(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div") is not None


def test_rescue_short_figure_labels_skips_div_with_image():
    html = '<div><p><img src="icon.png"></p></div><figure><img src="x.png"></figure>'
    result = _rescue_short_figure_labels(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div") is not None


def test_rescue_short_figure_labels_skips_negative_weight_div():
    html = (
        '<div class="sidebar"><p><strong>Short</strong></p></div>'
        '<figure><img src="x.png"></figure>'
    )
    result = _rescue_short_figure_labels(html)
    soup = _BS(result, "html.parser")
    assert soup.find("div") is not None


def test_rescue_short_figure_labels_survives_readability_min_text_length():
    # Regression test for the real system76 bug: a bold caption label
    # standing alone in its own <div> (not grouped with a section's
    # <h3>/intro paragraph, unlike the *first* label in a section) falls
    # under readability's min_text_length (25 chars) and is dropped, even
    # though the surrounding <figure>/<figcaption> content survives fine.
    from readability import Document

    html = (
        "<html><body><article>"
        "<p>Padding paragraph one to give the surrounding article enough "
        "weight for readability to pick it as the main candidate region.</p>"
        '<figure><img src="a.png">'
        "<figcaption>A reasonably long figure caption describing the first "
        "screenshot in plenty of descriptive detail.</figcaption></figure>"
        "<div><p><strong>Label two</strong></p></div>"
        '<figure><img src="b.png">'
        "<figcaption>Second screenshot in the sequence, also described in "
        "plenty of detail.</figcaption></figure>"
        "<p>Padding paragraph two, also long enough to keep this region "
        "scored as the main content candidate for readability's algorithm.</p>"
        "</article></body></html>"
    )

    without_fix = Document(html).summary() or ""
    assert "Label two" not in without_fix, (
        "fixture doesn't reproduce the readability drop — adjust padding"
    )

    fixed_html = _rescue_short_figure_labels(html)
    with_fix = Document(fixed_html).summary() or ""
    assert "Label two" in with_fix


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


# ---------------------------------------------------------------------------
# _find_dek_paragraph / _find_hero_image / _rescue_orphaned_header
# ---------------------------------------------------------------------------

_TITLE = "Popular Go Decimal Library Targeted by Long-Running Typosquat"


def test_find_dek_paragraph_returns_immediate_sibling():
    html = (
        "<div><h1>Title</h1>"
        "<p>A long-running Go typosquat impersonated the library.</p></div>"
    )
    soup = _BS(html, "html.parser")
    dek = _find_dek_paragraph(soup.find("h1"))
    assert dek is not None
    assert (
        dek.get_text(strip=True)
        == "A long-running Go typosquat impersonated the library."
    )


def test_find_dek_paragraph_skips_style_and_script_siblings():
    html = (
        "<div><h1>Title</h1>"
        "<style>.x{color:red}</style>"
        "<script>track()</script>"
        "<p>A long-running Go typosquat impersonated the library.</p>"
        "</div>"
    )
    soup = _BS(html, "html.parser")
    dek = _find_dek_paragraph(soup.find("h1"))
    assert dek is not None
    assert dek.name == "p"


def test_find_dek_paragraph_skips_whitespace_text_node_sibling():
    # Real-world HTML has whitespace text nodes between tags — must not be
    # mistaken for a non-<p> sibling that aborts the scan.
    html = "<div><h1>Title</h1>\n  <p>A long paragraph of real prose here.</p></div>"
    soup = _BS(html, "html.parser")
    dek = _find_dek_paragraph(soup.find("h1"))
    assert dek is not None
    assert dek.name == "p"


def test_find_dek_paragraph_none_when_first_sibling_is_not_p():
    # First substantive sibling is a <div> (e.g. a byline block) — must not
    # skip past it looking for a <p> further down.
    html = (
        "<div><h1>Title</h1><div>By Jane Doe</div>"
        "<p>A long paragraph of real prose here.</p></div>"
    )
    soup = _BS(html, "html.parser")
    assert _find_dek_paragraph(soup.find("h1")) is None


def test_find_dek_paragraph_none_when_too_short():
    html = "<div><h1>Title</h1><p>5 min read</p></div>"
    soup = _BS(html, "html.parser")
    assert _find_dek_paragraph(soup.find("h1")) is None


def test_find_dek_paragraph_none_without_following_sibling():
    html = "<div><h1>Title</h1></div>"
    soup = _BS(html, "html.parser")
    assert _find_dek_paragraph(soup.find("h1")) is None


def test_find_hero_image_matches_alt_several_levels_up():
    # socket.dev shape: hero <img> is a sibling of the title block several
    # ancestor levels above <h1>, alongside an unrelated avatar <img>.
    html = (
        "<div>"  # ancestor level 4
        f"<div><div><div><h1>{_TITLE}</h1></div></div></div>"
        '<div><img src="/avatar.jpg" alt="Kush Pandya"/></div>'
        f'<div><img src="/hero.png" alt="{_TITLE}"/></div>'
        "</div>"
    )
    soup = _BS(html, "html.parser")
    hero = _find_hero_image(soup.find("h1"), _TITLE)
    assert hero is not None
    assert hero.get("src") == "/hero.png"


def test_find_hero_image_none_when_no_alt_matches():
    html = (
        "<div>"
        f"<div><div><div><h1>{_TITLE}</h1></div></div></div>"
        '<div><img src="/avatar.jpg" alt="Kush Pandya"/></div>'
        "</div>"
    )
    soup = _BS(html, "html.parser")
    assert _find_hero_image(soup.find("h1"), _TITLE) is None


def test_find_hero_image_skips_image_without_alt():
    html = (
        "<div>"
        f"<div><div><div><h1>{_TITLE}</h1></div></div></div>"
        '<div><img src="/avatar.jpg"/></div>'
        f'<div><img src="/hero.png" alt="{_TITLE}"/></div>'
        "</div>"
    )
    soup = _BS(html, "html.parser")
    hero = _find_hero_image(soup.find("h1"), _TITLE)
    assert hero is not None
    assert hero.get("src") == "/hero.png"


def test_find_hero_image_none_when_depth_exceeded():
    # <h1> nested deeper than _HERO_SEARCH_MAX_DEPTH ancestor levels, with
    # no matching image and no <body> ever reached — the walk must give up
    # rather than loop forever or raise.
    html = f"<h1>{_TITLE}</h1>"
    for _ in range(10):
        html = f"<div>{html}</div>"
    soup = _BS(html, "html.parser")
    assert _find_hero_image(soup.find("h1"), _TITLE) is None


def test_find_hero_image_none_without_title():
    html = f'<div><h1>{_TITLE}</h1></div><img src="/hero.png" alt="{_TITLE}"/>'
    soup = _BS(html, "html.parser")
    assert _find_hero_image(soup.find("h1"), "") is None


def test_find_hero_image_matches_truncated_meta_title_prefix():
    # socket.dev shape: the <title> tag (source of trafilatura's meta.title)
    # is SEO-truncated with a trailing "…", but the hero <img>'s alt carries
    # the full headline. The plain similarity ratio undershoots 0.7 even
    # though the hero image is right there — the truncated-prefix fallback
    # catches it instead. Deliberately compares against the passed-in
    # *title*, not h1.get_text(): Substack renders the publication name as
    # the page's first <h1>, not the article's own headline, so matching
    # against h1 text directly would false-match a nearby publication-logo
    # <img> on that site (see test_rescue_orphaned_header_ignores_wrong_h1...
    # below for the regression this guards against).
    full_title = (
        "TrapDoor Crypto Stealer Supply Chain Attack Hits 34 Packages and "
        "Hundreds of Versions Across npm, PyPI, and Crates.io"
    )
    truncated_title = "TrapDoor Crypto Stealer Supply Chain Attack Hits 34 Packages..."
    assert (
        difflib.SequenceMatcher(
            None, truncated_title.lower(), full_title.lower()
        ).ratio()
        < _HERO_ALT_MATCH_RATIO
    )
    html = (
        "<div>"
        f"<div><div><div><h1>{full_title}</h1></div></div></div>"
        '<div><img src="/avatar.jpg" alt="Someone"/></div>'
        f'<div><img src="/hero.png" alt="{full_title}"/></div>'
        "</div>"
    )
    soup = _BS(html, "html.parser")
    hero = _find_hero_image(soup.find("h1"), truncated_title)
    assert hero is not None
    assert hero.get("src") == "/hero.png"


def test_find_hero_image_truncated_prefix_does_not_match_unrelated_alt():
    # The truncated-prefix fallback must still require an actual prefix
    # match — it isn't a blanket "truncated title present" bypass.
    html = (
        "<div><h1>Some Article...</h1></div>"
        '<div><img src="/logo.png" alt="Publication Logo"/></div>'
    )
    soup = _BS(html, "html.parser")
    assert _find_hero_image(soup.find("h1"), "Some Article...") is None


def test_find_hero_image_short_truncated_prefix_not_used_as_fallback():
    # A short de-ellipsized prefix (below _TRUNCATED_PREFIX_MIN_LEN) is not
    # accepted as a fallback match — real SEO truncation cuts a long
    # headline down to ~60 chars, so a short prefix is more likely to
    # spuriously prefix-match an unrelated image's alt text.
    html = (
        "<div><h1>Q&A...</h1></div>"
        '<div><img src="/unrelated.png" alt="Q&A session banner graphic"/></div>'
    )
    soup = _BS(html, "html.parser")
    assert _find_hero_image(soup.find("h1"), "Q&A...") is None


def test_rescue_orphaned_header_milkroad_shape_prepends_dek():
    # MilkRoad: <h1> and dek <p> share a header div that readability scores
    # separately from (and loses to) the real body div — dek never reaches
    # the extracted content.
    body_div = '<div class="richtext"><p>GM. This is Milk Road newsletter.</p></div>'
    raw_html = (
        "<main>"
        '<div class="header"><h1>Semis, Bitcoin, and the rally</h1>'
        "<p>Leverage on chip stocks unwound. The economy holds strong.</p></div>"
        f"{body_div}"
        "</main>"
    )
    result = _rescue_orphaned_header(
        raw_html, body_div, "Semis, Bitcoin, and the rally"
    )
    soup = _BS(result, "html.parser")
    assert soup.find("p").get_text(strip=True) == (
        "Leverage on chip stocks unwound. The economy holds strong."
    )
    assert "GM. This is Milk Road" in result


def test_rescue_orphaned_header_socket_shape_prepends_dek_and_hero():
    raw_html = (
        "<div>"
        f"<div><div><div><h1>{_TITLE}</h1>"
        "<p>A long-running Go typosquat impersonated the library.</p></div></div></div>"
        '<div><img src="/avatar.jpg" alt="Kush Pandya"/></div>'
        f'<div><img src="/hero.png" alt="{_TITLE}"/></div>'
        '<div class="prose"><p>Socket identified a malicious Go module.</p></div>'
        "</div>"
    )
    content = '<div class="prose"><p>Socket identified a malicious Go module.</p></div>'
    result = _rescue_orphaned_header(raw_html, content, _TITLE)
    soup = _BS(result, "html.parser")
    assert soup.find("p").get_text(strip=True) == (
        "A long-running Go typosquat impersonated the library."
    )
    img = soup.find("img")
    assert img is not None
    assert img.get("src") == "/hero.png"
    assert "Socket identified a malicious Go module" in result


def test_rescue_orphaned_header_ignores_wrong_h1_publication_name():
    # Substack shape: the page's *first* <h1> is the publication name in the
    # site header, not the article's own headline — soup.find("h1") in
    # _rescue_orphaned_header picks that one. If hero matching compared
    # against that h1's own text instead of the passed-in article *title*,
    # it would false-match the small publication-logo <img> sitting nearby.
    # Regression guard for that failure mode (caught 2026-07-22).
    raw_html = (
        "<div>"
        "<div><h1>Viennese Civilization</h1>"
        '<div><img src="/logo.png" alt="Viennese Civilization"/></div></div>'
        '<div class="body"><h1>Money-Demand is very different</h1>'
        "<p>Article body text long enough to survive.</p></div>"
        "</div>"
    )
    content = '<div class="body"><p>Article body text long enough to survive.</p></div>'
    result = _rescue_orphaned_header(
        raw_html, content, "Money-Demand is very different"
    )
    assert "/logo.png" not in result


def test_rescue_orphaned_header_noop_without_h1():
    content = "<div><p>Body.</p></div>"
    result = _rescue_orphaned_header(
        "<div><p>No heading here.</p></div>", content, "Title"
    )
    assert result == content


def test_rescue_orphaned_header_dedups_dek_already_in_content():
    body_div = '<div class="body"><h1>Title</h1><p>Already in the winner.</p></div>'
    raw_html = "<div><h1>Title</h1><p>Already in the winner.</p></div>" + body_div
    result = _rescue_orphaned_header(raw_html, body_div, "Title")
    assert result == body_div


def test_rescue_orphaned_header_dedups_hero_already_in_content():
    body_div = (
        f'<div class="body"><img src="/hero.png" alt="{_TITLE}"/>'
        "<p>Body text long enough.</p></div>"
    )
    raw_html = (
        f"<div><h1>{_TITLE}</h1></div>"
        f'<div><img src="/hero.png" alt="{_TITLE}"/></div>'
        f"{body_div}"
    )
    result = _rescue_orphaned_header(raw_html, body_div, _TITLE)
    assert result == body_div
