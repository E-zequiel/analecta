"""Resolves classic Twitter/X widget embeds inside article HTML.

Sites that embed a tweet via X's own publish widget leave either a
``<blockquote class="twitter-tweet">`` (the oEmbed fallback markup, present
in server-rendered HTML) or, less commonly, a direct
``<iframe src="https://platform.x.com/embed/Tweet.html?id=...">`` in
already-cached/server-rendered HTML — ``widgets.js`` normally injects that
iframe client-side to replace the blockquote, which the extraction pipeline
never executes (it never runs page JavaScript), so the iframe form is only
reachable here when a site's own build process pre-renders it. Both forms
carry the tweet id in plain, unexecuted markup.

Confirmed empirically (this module's design phase) that readability-lxml
drops the raw ``blockquote.twitter-tweet`` outright (its link-density
pruning rule) and trafilatura sometimes keeps it but corrupts it (the
tweet's own attached link comes out delinked, the author/date line gets
interleaved mid-sentence) — which extractor wins depends on the 1.5x length
ratio `article.py` uses, so today's behavior is already inconsistent, not a
hypothetical risk. This module runs *before* either extractor sees the HTML,
replacing each embed with pre-rendered, low-link-density markup that survives
scoring regardless of which extractor wins.

Reuses `x.py`'s syndication fetch and single-tweet renderer verbatim — no
adaptation needed, since neither touches the Upward reply-chain walk (that
walk lives only in `_walk_reply_chain`/`XExtractor.extract`, never called
here). An inline embed always renders as just the one tweet, standalone.
"""

import asyncio
import html
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

# Deliberate cross-module reuse of x.py's private helpers — same tweet-id
# parsing, syndication fetch, and single-tweet renderer as the standalone
# X extractor, so an inline embed stays byte-for-byte consistent with one
# (module docstring above explains why no reply-chain coupling exists).
from analecta.extraction.x import (
    _extract_tweet_id,  # pyright: ignore[reportPrivateUsage]
    _fetch_syndication,  # pyright: ignore[reportPrivateUsage]
    _render_tweet_html,  # pyright: ignore[reportPrivateUsage]
)

_MAX_CONCURRENT = 5
_IFRAME_HOSTS = frozenset({"platform.twitter.com", "platform.x.com"})
_VIEW_ON_X_TEXT = "View tweet on X"


def _iframe_tweet_id(src: str) -> str | None:
    """Return the tweet id from a ``platform.{twitter,x}.com/embed/Tweet.html`` src.

    Args:
        src: The iframe's ``src`` attribute value.

    Returns:
        The numeric tweet id, or ``None`` if *src* isn't a matching embed URL.
    """
    parsed = urlparse(src)
    if parsed.hostname not in _IFRAME_HOSTS:
        return None
    if "Tweet.html" not in parsed.path:
        return None
    ids = parse_qs(parsed.query).get("id")
    return ids[0] if ids else None


def _permalink_from_blockquote(blockquote: Tag) -> str | None:
    """Return the tweet permalink href from a fallback blockquote's trailing anchor.

    Twitter's oEmbed fallback always closes with ``<a href=".../status/id">``
    (the visible date/timestamp link) — the last ``<a>`` in the blockquote
    whose href actually contains a status id, tolerant of any anchors that
    might appear earlier for mentions/hashtags inside the tweet body itself.
    """
    for a in reversed(blockquote.find_all("a")):
        href = a.get("href")
        if href and _extract_tweet_id(href):
            return href
    return None


def _reshape_fallback_blockquote(blockquote: Tag) -> str:
    """Rebuild a fallback ``blockquote.twitter-tweet`` as low-link-density markup.

    Used only when the syndication fetch fails (deleted/protected tweet,
    rate limit, network blip) — the blockquote's own oEmbed fallback text is
    already present in the DOM, so this is a pure string reshape, no second
    network call. Collapses the whole blockquote (tweet text plus the
    "&mdash; Author (@handle)" attribution tail) into one escaped plain-text
    paragraph rather than trying to recover the author's name/handle from
    that tail text: X's exact wording there isn't a stable contract (locale/
    `lang` param can format it differently), while a real permalink ``<a>``
    is guaranteed by the oEmbed contract itself. Trades the bold-author
    formatting `_render_tweet_html` gives a successful fetch for something
    that depends only on structure, not incidental text conventions — still
    strictly better than the original, which is reliably dropped or mangled
    by readability/trafilatura.

    Args:
        blockquote: The original ``<blockquote class="twitter-tweet">`` tag.

    Returns:
        HTML fragment: one paragraph of plain tweet text, plus a
        "View tweet on X" permalink paragraph when a permalink is found.
    """
    text = blockquote.get_text(" ", strip=True)
    parts = [f"<p>{html.escape(text)}</p>"]
    permalink = _permalink_from_blockquote(blockquote)
    if permalink:
        parts.append(_view_on_x_link(permalink))
    return "".join(parts)


def _view_on_x_link(href: str) -> str:
    return f'<p><a href="{html.escape(href, quote=True)}">{_VIEW_ON_X_TEXT}</a></p>'


def _iframe_fallback_link(tweet_id: str) -> str:
    """Return a bare "View tweet on X" link for an iframe embed with no fallback.

    Unlike the blockquote case, a raw iframe embed carries no fallback text
    at all in static HTML — nothing to reshape — so a fetch failure here can
    only degrade to a permalink, built from the id alone (X resolves
    ``/i/web/status/<id>`` without needing the author's handle).
    """
    return _view_on_x_link(f"https://x.com/i/web/status/{tweet_id}")


async def resolve_embedded_tweets(raw_html: str) -> str:
    """Replace classic Twitter/X widget embeds with rendered tweet content.

    Scans *raw_html* for ``blockquote.twitter-tweet`` elements and
    ``platform.{twitter,x}.com/embed/Tweet.html`` iframes, fetches each
    referenced tweet concurrently (bounded by a semaphore, same idiom
    `AssetDownloader` uses for image downloads), and replaces each embed
    in place with `_render_tweet_html`'s output. A tweet that fails to fetch
    (deleted, rate-limited, network error — `_fetch_syndication` never
    raises, only returns ``None``) degrades to a reshaped version of the
    blockquote's own already-present fallback text, or a bare permalink for
    the iframe case, rather than leaving the original markup for
    readability/trafilatura to drop or mangle.

    A no-op, no-network-call fast path when *raw_html* has no matching
    embeds — the overwhelming majority of articles.

    Args:
        raw_html: Raw (or already partially cleaned) article HTML.

    Returns:
        *raw_html* with every matched embed replaced. Unchanged if none are
        found.
    """
    soup = BeautifulSoup(raw_html, "html.parser")
    blockquotes = soup.find_all("blockquote", class_="twitter-tweet")
    iframes = [
        el for el in soup.find_all("iframe") if _iframe_tweet_id(el.get("src") or "")
    ]

    if not blockquotes and not iframes:
        return raw_html

    targets: list[tuple[Tag, str]] = []
    for bq in blockquotes:
        tweet_id = _extract_tweet_id(str(bq))
        if tweet_id:
            targets.append((bq, tweet_id))
    for ifr in iframes:
        tweet_id = _iframe_tweet_id(ifr.get("src") or "")
        if tweet_id:
            targets.append((ifr, tweet_id))

    if not targets:
        return raw_html

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _bounded_fetch(tweet_id: str):
        async with sem:
            return await _fetch_syndication(tweet_id)

    tweets = await asyncio.gather(
        *[_bounded_fetch(tweet_id) for _, tweet_id in targets]
    )

    for (element, tweet_id), tweet in zip(targets, tweets, strict=True):
        if tweet is not None:
            replacement = f"<blockquote>{_render_tweet_html(tweet)}</blockquote>"
        elif element.name == "blockquote":
            replacement = _reshape_fallback_blockquote(element)
        else:
            replacement = _iframe_fallback_link(tweet_id)
        new_nodes = list(BeautifulSoup(replacement, "html.parser").contents)
        element.replace_with(*new_nodes)

    return str(soup)
