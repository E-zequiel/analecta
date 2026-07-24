"""SSRF blocklist for Tier 1's direct fetches.

Ported from ``electron/main/scraper.ts``'s ``validateScrapeUrl`` — the only
SSRF guard in the app before Tier 2's removal, and one that only ever covered
Tier 2's single render-server fetch. Now that Tier 1's own ``httpx2`` calls
are the only fetch path left, this closes the gap for all three of its
direct-fetch call sites: the pasted URL itself (``article.py``, ``social.py``),
redirect targets Tier 1 follows on the way to that URL, and remote image URLs
discovered in already-fetched page content (``assets.py``) — the last being
attacker-controlled, not user-chosen.

Matches the ported logic exactly: only http(s) schemes are allowed; blocked
hosts are ``localhost``, loopback (``127.0.0.0/8``, ``::1``, and the
IPv4-mapped IPv6 form of the former), link-local (``169.254.0.0/16`` and its
IPv4-mapped IPv6 form), and RFC 1918 private ranges. A DNS name that itself
resolves to one of these (rather than being one as a literal) is not caught —
inherited limitation from the source logic this replaces.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse

import httpx2

from analecta.extraction.core import ExtractionError

_BLOCKED_V4_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def _is_blocked_host(hostname: str) -> bool:
    """Return True if *hostname* is a loopback/link-local/private literal.

    Args:
        hostname: A URL's hostname component (already lowercased by
            ``urllib.parse``, but bracket-stripped here in case an IPv6
            literal reaches this function directly rather than via
            ``urlparse``).

    Returns:
        True for ``localhost``, ``::1``, any IPv4-mapped IPv6 address whose
        unwrapped IPv4 form is blocked, or any address in
        ``_BLOCKED_V4_NETWORKS``. False for a non-IP hostname (DNS
        resolution is not performed here).
    """
    host = hostname.strip("[]")
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip == ipaddress.IPv6Address("::1"):
            return True
        mapped = ip.ipv4_mapped
        if mapped is None:
            return False
        ip = mapped
    return any(ip in net for net in _BLOCKED_V4_NETWORKS)


def validate_fetch_url(url: str) -> None:
    """Raise if *url* targets a blocked scheme or host.

    Args:
        url: A URL Tier 1 is about to fetch directly — the pasted URL, a
            redirect target, or a remote image URL found in fetched content.

    Raises:
        ExtractionError: If *url*'s scheme isn't ``http``/``https``, or its
            host is loopback, link-local, or RFC 1918 private.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ExtractionError(f"Blocked fetch to unsupported scheme: {url!r}")
    if not parsed.hostname or _is_blocked_host(parsed.hostname):
        raise ExtractionError(f"Blocked fetch to local/internal address: {url!r}")


async def block_redirect_to_internal(response: httpx2.Response) -> None:
    """httpx2 ``"response"`` event hook: validate a redirect before it's followed.

    Fires for every response in a redirect chain, including the terminal
    one. Only a response carrying a ``Location`` header (i.e. an actual
    redirect) triggers a check — resolved against ``response.url`` the same
    way a browser resolves a relative ``Location``, then validated before
    httpx2 sends a request to it. Must be ``async def`` — ``AsyncClient``
    always does ``await hook(response)``, and awaiting a plain sync
    function's ``None`` return raises ``TypeError`` on every response this
    hook is a no-op for (i.e. every non-redirect response).

    Args:
        response: The response httpx2 just received, prior to following any
            redirect it describes.

    Raises:
        ExtractionError: If the redirect target is a blocked scheme or host.
    """
    location = response.headers.get("location")
    if location:
        validate_fetch_url(urljoin(str(response.url), location))
