import html
import math
import re
from typing import Any

import httpx2
from bs4 import BeautifulSoup, Tag

from analecta.extraction.core import ExtractedContent, ExtractionError, SourceExtractor
from analecta.extraction.http_identity import build_headers

_STATUS_ID_RE = re.compile(
    r"(?:twitter\.com|x\.com)/(?:[^/?#]+/status(?:es)?|i/web/status)/(\d+)",
    re.IGNORECASE,
)
_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
_OEMBED_URL = "https://publish.twitter.com/oembed"
_TIMEOUT = 8.0
_MAX_HOPS = 100
_TITLE_MAX = 80
_BASE36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_INCOMPLETE_THREAD_NOTE = (
    "<p><em>Thread may be incomplete — could not fetch earlier tweets in this "
    "chain.</em></p>"
)
_OEMBED_FALLBACK_NOTE = (
    "<p><em>Fetched via oEmbed fallback — media unavailable.</em></p>"
)


def _extract_tweet_id(url: str) -> str | None:
    """Parse a tweet id out of an X/Twitter status URL.

    Tolerant of ``x.com``/``twitter.com`` (any subdomain, since matching is
    substring-based on the host+path), ``/status/`` and the older
    ``/statuses/``, the id-only ``/i/web/status/<id>`` form, and trailing
    query params.

    Args:
        url: X/Twitter status URL.

    Returns:
        The numeric tweet id, or ``None`` if the URL doesn't contain one.
    """
    m = _STATUS_ID_RE.search(url)
    return m.group(1) if m else None


def _to_base36_int(n: int) -> str:
    if n == 0:
        return "0"
    digits: list[str] = []
    while n:
        n, r = divmod(n, 36)
        digits.append(_BASE36_DIGITS[r])
    return "".join(reversed(digits))


def _syndication_token(tweet_id: str) -> str:
    """Compute the syndication endpoint's request token for *tweet_id*.

    Python port of the token react-tweet (and X's own embed widget) send:
    ``((id / 1e15) * pi).toString(36)`` with zero-runs and the decimal point
    stripped. JavaScript's ``Number.prototype.toString(36)`` has no direct
    Python equivalent for a float, so the integer and fractional parts are
    converted to base 36 separately and re-joined before stripping.

    Args:
        tweet_id: Numeric tweet id as a string.

    Returns:
        The token string to send as the ``token`` query parameter.
    """
    value = (int(tweet_id) / 1e15) * math.pi
    sign = "-" if value < 0 else ""
    value = abs(value)
    int_part = int(value)
    frac_part = value - int_part

    frac_digits: list[str] = []
    f = frac_part
    for _ in range(30):
        f *= 36
        digit = int(f)
        frac_digits.append(_BASE36_DIGITS[digit])
        f -= digit
        if f <= 0:
            break

    raw = _to_base36_int(int_part) + ("." + "".join(frac_digits) if frac_digits else "")
    return re.sub(r"(0+|\.)", "", sign + raw)


async def _fetch_syndication(tweet_id: str) -> dict[str, Any] | None:
    """Fetch a tweet from X's syndication endpoint.

    Args:
        tweet_id: Numeric tweet id.

    Returns:
        The parsed tweet dict, or ``None`` on any non-200 response,
        malformed JSON, or a body without a ``"text"`` field — the latter
        catches the real ``TweetTombstone`` shape (deleted/protected/
        unavailable tweets) without needing to allowlist every
        ``__typename`` this undocumented endpoint might return. Never
        raises.
    """
    token = _syndication_token(tweet_id)
    try:
        async with httpx2.AsyncClient(
            timeout=_TIMEOUT, headers=build_headers("api")
        ) as client:
            resp = await client.get(
                _SYNDICATION_URL,
                params={"id": tweet_id, "token": token, "lang": "en"},
            )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or "text" not in data:
        return None
    return data


async def _fetch_oembed(url: str) -> dict[str, Any] | None:
    """Fetch a tweet's oEmbed representation as a fallback.

    Args:
        url: The original tweet URL.

    Returns:
        The parsed oEmbed dict, or ``None`` on failure. Never raises.
    """
    try:
        async with httpx2.AsyncClient(
            timeout=_TIMEOUT, headers=build_headers("api")
        ) as client:
            resp = await client.get(
                _OEMBED_URL, params={"url": url, "omit_script": "true"}
            )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or "html" not in data:
        return None
    return data


def _utf16_offset_to_codepoint(text: str, utf16_offset: int) -> int:
    """Convert a UTF-16 code-unit offset into *text* to a Python codepoint index.

    ``display_text_range`` is expressed in UTF-16 code units (the classic
    Twitter/JS convention — a JS string is UTF-16). Python strings index by
    codepoint, so astral characters (anything needing a surrogate pair, e.g.
    most emoji) make the two counting schemes diverge. Live-verified against
    a real tweet containing astral emoji before the cut point — see
    ``x.com/duolingo/status/848097704869851136``.

    Args:
        text: The tweet's raw ``text`` field.
        utf16_offset: An offset from ``display_text_range``, in UTF-16 code units.

    Returns:
        The equivalent Python codepoint index into *text*.
    """
    cumulative = 0
    for i, ch in enumerate(text):
        if cumulative >= utf16_offset:
            return i
        cumulative += len(ch.encode("utf-16-le")) // 2
    return len(text)


def _display_text(tweet: dict[str, Any]) -> str:
    """Slice a tweet's ``text`` to its displayed portion per ``display_text_range``."""
    text = tweet.get("text") or ""
    range_ = tweet.get("display_text_range")
    if not range_:
        return text
    start_cp = _utf16_offset_to_codepoint(text, range_[0])
    end_cp = _utf16_offset_to_codepoint(text, range_[1])
    return text[start_cp:end_cp]


def _clean_tweet_text(tweet: dict[str, Any]) -> str:
    r"""Build the HTML-safe, linkified displayed text for one tweet.

    Trims ``text`` to ``display_text_range`` (dropping the trailing
    ``t.co`` link to the tweet's own attached media, if any), then replaces
    any ``entities.urls[]`` / ``entities.hashtags[]`` span still inside that
    range with a real ``<a>`` tag — expanded URL for links, an
    ``x.com/hashtag/...`` search link for hashtags (kept out of Analecta's
    own ``#hashtag`` backlink parser: a hashtag rendered inside a Markdown
    link's label is never preceded by whitespace, which the parser's
    ``(?<!\\S)`` lookbehind requires — verified against the real parser
    during planning).

    Note: unlike ``display_text_range``, ``entities.*.indices`` are already
    in Python-codepoint units, not UTF-16 — live-verified on the same
    Duolingo tweet. No further conversion is applied to them here, only a
    shift into the sliced text's local coordinates.

    X's ``text`` field carries the author's real line breaks verbatim
    (live-verified: a tweet with visible paragraph breaks on x.com comes
    back with literal ``\n``/``\n\n`` in ``text``). Left as plain ``\n``,
    they'd sit inside a single ``<p>`` and markdownify collapses any
    whitespace run in a text node to one ``\n`` — a Markdown soft break,
    which renders as a plain space unless the renderer opts into
    ``breaks: true``. Converting each ``\n`` to ``<br>`` here makes
    markdownify emit a real hard break (two trailing spaces) instead, so
    the line break survives regardless of the renderer's soft-break
    setting.

    Args:
        tweet: A syndication tweet dict.

    Returns:
        HTML-safe text (plain text escaped, entities replaced by ``<a>``
        tags, line breaks turned into ``<br>``).
    """
    text = tweet.get("text") or ""
    range_ = tweet.get("display_text_range")
    start_u16 = range_[0] if range_ else 0
    start_cp = _utf16_offset_to_codepoint(text, start_u16)
    displayed = _display_text(tweet)
    end_cp = start_cp + len(displayed)

    entities = tweet.get("entities") or {}
    spans: list[tuple[int, int, str]] = []

    for u in entities.get("urls") or []:
        indices = u.get("indices")
        if not indices:
            continue
        s, e = indices
        if not (start_cp <= s and e <= end_cp):
            continue
        href = u.get("expanded_url") or u.get("url") or ""
        label = u.get("display_url") or href
        fragment = f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'
        spans.append((s - start_cp, e - start_cp, fragment))

    for h in entities.get("hashtags") or []:
        indices = h.get("indices")
        if not indices:
            continue
        s, e = indices
        if not (start_cp <= s and e <= end_cp):
            continue
        tag = h.get("text") or ""
        href = f"https://x.com/hashtag/{tag}"
        fragment = f'<a href="{html.escape(href, quote=True)}">#{html.escape(tag)}</a>'
        spans.append((s - start_cp, e - start_cp, fragment))

    spans.sort(key=lambda span: span[0])
    pieces: list[str] = []
    cursor = 0
    for s, e, fragment in spans:
        pieces.append(html.escape(displayed[cursor:s]))
        pieces.append(fragment)
        cursor = e
    pieces.append(html.escape(displayed[cursor:]))
    return "".join(pieces).replace("\n", "<br>")


def _tweet_permalink(tweet: dict[str, Any]) -> str:
    user = tweet.get("user") or {}
    screen_name = user.get("screen_name") or "i"
    return f"https://x.com/{screen_name}/status/{tweet.get('id_str', '')}"


def _render_tweet_html(tweet: dict[str, Any]) -> str:
    """Render one tweet (and its quoted tweet, if any) to an HTML block.

    Video and animated-GIF media are rendered as a link out to the tweet's
    media permalink rather than downloaded: X does not serve real ``.gif``
    files (an ``animated_gif``-typed entry is a static ``.jpg`` poster plus
    a ``video/mp4`` — live-verified), and ``AssetDownloader`` rejects
    non-image ``Content-Type`` by design.

    Args:
        tweet: A syndication tweet dict.

    Returns:
        An HTML fragment for this tweet.
    """
    parts = [f"<p>{_clean_tweet_text(tweet)}</p>"]

    for photo in tweet.get("photos") or []:
        src = photo.get("url")
        if src:
            parts.append(f'<img src="{html.escape(src, quote=True)}" alt="">')

    for media in tweet.get("mediaDetails") or []:
        if media.get("type") in ("video", "animated_gif"):
            link = media.get("expanded_url") or _tweet_permalink(tweet)
            parts.append(
                f'<p><a href="{html.escape(link, quote=True)}">View video on X</a></p>'
            )

    quoted = tweet.get("quoted_tweet")
    if quoted:
        parts.append(f"<blockquote>{_render_tweet_html(quoted)}</blockquote>")

    return "".join(parts)


async def _walk_reply_chain(
    tweet: dict[str, Any], max_hops: int = _MAX_HOPS
) -> tuple[list[dict[str, Any]], bool]:
    """Walk upward from *tweet*, collecting its reply-to-parent ancestors.

    Always fetches and includes the immediate parent, regardless of
    authorship. Continues walking only while each new parent shares the
    same author as the tweet that replied to it; the moment a parent's
    author differs, that one tweet is included and the walk stops — a walk
    can never include more than one cross-author tweet, since including one
    is exactly the condition that ends it. This makes pasting the last
    tweet of a same-author thread pull in the entire thread, while pasting
    a reply to someone else's tweet pulls in exactly that one tweet of
    context.

    Args:
        tweet: The originally-fetched tweet (already includes its own
            ``in_reply_to_status_id_str`` if it's a reply).
        max_hops: Safety cap on chain length, sized purely to bound
            pathological/abusive input, not as a feature limit — real
            threads should never approach it.

    Returns:
        A tuple of ``(chain, complete)``. ``chain`` is root-first and
        always includes at least *tweet*. ``complete`` is ``True`` when the
        walk ended cleanly (root reached, or a cross-author boundary was
        included and the walk stopped there) and ``False`` when it ended
        ambiguously (a hop's fetch failed — which may mean a deleted
        parent, or may mean a transient failure against an endpoint known
        to fail unpredictably) or hit ``max_hops``.
    """
    chain = [tweet]
    current = tweet
    hops = 0
    while hops < max_hops:
        parent_id = current.get("in_reply_to_status_id_str")
        if not parent_id:
            return list(reversed(chain)), True

        parent = await _fetch_syndication(parent_id)
        if parent is None:
            return list(reversed(chain)), False

        chain.append(parent)
        hops += 1

        parent_user = parent.get("user") or {}
        current_user = current.get("user") or {}
        if parent_user.get("id_str") != current_user.get("id_str"):
            return list(reversed(chain)), True

        current = parent

    return list(reversed(chain)), False


def _format_author(tweet: dict[str, Any]) -> str:
    user = tweet.get("user") or {}
    name = user.get("name") or ""
    screen_name = user.get("screen_name") or ""
    if name and screen_name:
        return f"{name} (@{screen_name})"
    return name or screen_name


def _build_title(text: str, fallback: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return fallback
    if len(collapsed) > _TITLE_MAX:
        return collapsed[:_TITLE_MAX].rstrip() + "…"
    return collapsed


def _oembed_paragraphs(data: dict[str, Any]) -> list[str]:
    soup = BeautifulSoup(data.get("html") or "", "html.parser")
    blockquote = soup.find("blockquote")
    if isinstance(blockquote, Tag):
        texts = [p.get_text() for p in blockquote.find_all("p")]
        if texts:
            return texts
    text = soup.get_text(strip=True)
    return [text] if text else []


class XExtractor(SourceExtractor):
    """Extracts a tweet (and its Upward reply chain) via X's syndication endpoint.

    Falls back to the official oEmbed endpoint (text/author only, no media)
    when syndication is unavailable — a hedge against the syndication
    endpoint itself drifting/breaking, not against IP-based blocking
    (nothing unauthenticated protects against that). No headless browser
    (Tier 2) is used anywhere in this path.
    """

    async def extract(self, url: str) -> ExtractedContent:
        """Extract a tweet plus its Upward reply chain from *url*.

        Args:
            url: X/Twitter status URL.

        Returns:
            ``ExtractedContent`` with ``source_type="x"``.

        Raises:
            ExtractionError: If the URL has no parseable tweet id, or both
                the syndication and oEmbed endpoints fail.
        """
        tweet_id = _extract_tweet_id(url)
        if not tweet_id:
            raise ExtractionError(f"Could not parse a tweet id from URL: {url}")

        tweet = await _fetch_syndication(tweet_id)
        if tweet is not None:
            chain, complete = await _walk_reply_chain(tweet)
            root = chain[0]

            html_parts: list[str] = []
            if not complete:
                html_parts.append(_INCOMPLETE_THREAD_NOTE)
            html_parts.extend(_render_tweet_html(t) for t in chain)

            metadata: dict[str, Any] = {
                "author": _format_author(root),
                "published": root.get("created_at"),
                "platform": "x",
                "tweet_id": tweet_id,
                "fetch_method": "syndication",
            }
            if len(chain) > 1:
                metadata["thread_length"] = len(chain)

            return ExtractedContent(
                title=_build_title(_display_text(root), f"Tweet {tweet_id}"),
                html="".join(html_parts),
                url=url,
                source_type="x",
                metadata=metadata,
            )

        oembed = await _fetch_oembed(url)
        if oembed is not None:
            paragraphs = _oembed_paragraphs(oembed)
            body_html = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
            author_name = oembed.get("author_name") or ""

            return ExtractedContent(
                title=_build_title(" ".join(paragraphs), f"Tweet {tweet_id}"),
                html=_OEMBED_FALLBACK_NOTE + body_html,
                url=url,
                source_type="x",
                metadata={
                    "author": author_name,
                    "platform": "x",
                    "tweet_id": tweet_id,
                    "fetch_method": "oembed_fallback",
                },
            )

        raise ExtractionError(
            f"Could not extract tweet {tweet_id}: both the syndication and "
            "oEmbed endpoints failed."
        )
