"""SSRF blocklist for the extraction pipeline's direct fetches.

Guards every direct fetch of a URL not otherwise constrained by user choice:
the pasted URL itself (``article.py``, ``social.py``), redirect targets
followed on the way to that URL, and remote image URLs discovered in
already-fetched page content (``assets.py``) — the last being
attacker-controlled, not user-chosen.

Only http(s) schemes are allowed. Blocked hosts are ``localhost`` and any
address literal (IPv4, or IPv6 including its IPv4-mapped form) that
``ipaddress`` classifies as loopback, link-local, private (which covers RFC
1918 and its IPv6 analogue ``fc00::/7``), reserved, or unspecified
(``0.0.0.0``, ``::``). A DNS name that itself resolves to one of these
(rather than being one as a literal) is not caught — inherited limitation
from the source logic this replaces.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse

import httpx2

from analecta.extraction.core import ExtractionError


def _is_blocked_host(hostname: str) -> bool:
    """Return True if *hostname* is a loopback/link-local/private literal.

    Args:
        hostname: A URL's hostname component (already lowercased by
            ``urllib.parse``, but bracket-stripped here in case an IPv6
            literal reaches this function directly rather than via
            ``urlparse``).

    Returns:
        True for ``localhost`` or any address literal (unwrapping an
        IPv4-mapped IPv6 address to its IPv4 form first) that ``ipaddress``
        classifies as loopback, link-local, private, reserved, or
        unspecified. False for a non-IP hostname (DNS resolution is not
        performed here).
    """
    host = hostname.strip("[]")
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_fetch_url(url: str) -> None:
    """Raise if *url* targets a blocked scheme or host.

    Args:
        url: A URL about to be fetched directly — the pasted URL, a
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
