"""SSRF guard for the extraction pipeline's direct fetches.

Guards every direct fetch of a URL not otherwise constrained by user choice:
the pasted URL itself (``article.py``, ``social.py``), redirect targets
followed on the way to that URL, and remote image URLs discovered in
already-fetched page content (``assets.py``) — the last being
attacker-controlled, not user-chosen.

Only http(s) schemes are allowed. The real gate validates the *resolved
address*, not the hostname string: ``fetch_safely``/``fetch_pinned_once``
resolve the host themselves, reject if any returned address is loopback,
link-local, private (RFC 1918 and its IPv6 analogue ``fc00::/7``),
reserved, unspecified (``0.0.0.0``, ``::``), or multicast, then connect
directly to the one validated address they picked — the resolver is never
consulted again for that request. This closes both a hostname string that
encodes a blocked address in a form ``ipaddress`` doesn't parse but the
platform resolver still does (decimal/hex/octal IPv4, short-dotted
``127.1``, a trailing-dot ``localhost.``), and a DNS name whose answer
changes between the check and the connection (rebinding) — both classes
only exist if something validates a string and then lets the resolver run
again independently of that check. TLS verification still targets the
original hostname (via the ``sni_hostname`` request extension), not the
pinned address, so certificate checking is unaffected by the pin.

``validate_fetch_url`` is kept as a cheap, synchronous, non-authoritative
pre-filter — it only recognizes a blocked address already in canonical
``ipaddress`` form, with no resolution. Every actual fetch goes through
``fetch_safely`` or ``fetch_pinned_once``, which re-validate via resolution
regardless of what this pre-filter decided.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import httpx2

from analecta.extraction.core import ExtractionError

_ALLOWED_SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MAX_REDIRECTS = 20
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *ip* must never be fetched from.

    Args:
        ip: A concrete address — either a literal parsed from a URL or one
            returned by DNS resolution.

    Returns:
        True for any address that isn't allocated for public use per
        ``ipaddress``'s own ``is_global`` (this covers loopback, link-local,
        private — RFC 1918 and its IPv6 analogue ``fc00::/7`` — reserved,
        unspecified, CGNAT ``100.64.0.0/10``, and the benchmarking/
        documentation ranges, as a default-deny rather than an enumerated
        allowlist of what to catch), plus multicast explicitly — verified
        empirically that ``is_global`` returns ``True`` for multicast
        addresses on both address families, so it does not subsume that
        case. Unwraps an IPv4-mapped IPv6 address (``::ffff:0:0/96``), a
        NAT64-embedded one (the well-known ``64:ff9b::/96`` prefix a
        NAT64/DNS64 gateway on an IPv6-only network embeds an IPv4 address
        in), or a deprecated IPv4-compatible one (the bare ``::/96`` prefix,
        e.g. ``::127.0.0.1`` — distinguished from the other two forms by its
        embedded value never exceeding ``2**32 - 1``, while both of those
        stay well above it, so there's no overlap between the three checks)
        to its IPv4 form first — verified empirically that ``is_global``
        returns ``True`` for the embedded address under all three prefixes,
        so none of them is caught without unwrapping first. No OS on this
        project's target platforms actually routes the IPv4-compatible form
        to the address it embeds (RFC 4291 deprecated the automatic
        tunneling that once made it), but the classification itself is what
        this function promises to get right, independent of what a kernel
        happens to do with the result.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
        elif ip in _NAT64_PREFIX:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif int(ip) < 2**32:
            ip = ipaddress.IPv4Address(int(ip))
    return not ip.is_global or ip.is_multicast


def _is_blocked_host(hostname: str) -> bool:
    """Return True if *hostname* is ``localhost`` or a blocked address literal.

    Non-authoritative — see module docstring. Only recognizes canonical
    ``ipaddress`` forms; a hostname this can't parse as an IP literal is
    reported as not-blocked without further checking.

    Args:
        hostname: A URL's hostname component (already lowercased by
            ``urllib.parse``, but bracket-stripped here in case an IPv6
            literal reaches this function directly rather than via
            ``urlsplit``).

    Returns:
        True for ``localhost`` or any address literal ``ipaddress``
        classifies as blocked (see ``_is_blocked_address``). False for a
        non-IP hostname or a numeric form ``ipaddress`` doesn't parse.
    """
    host = hostname.strip("[]")
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return _is_blocked_address(ip)


def validate_fetch_url(url: str) -> None:
    """Raise if *url* targets a blocked scheme, or an obviously-blocked host.

    Cheap, synchronous, non-authoritative — see module docstring.
    ``fetch_safely``/``fetch_pinned_once`` perform the real, resolution-based
    check on every actual fetch; this exists only as a fast pre-check for a
    caller that wants to reject something before doing other work (e.g.
    ``social.py`` short-circuiting an inbox URL that's obviously bad before
    issuing a request at all).

    Args:
        url: A URL a caller is about to validate.

    Raises:
        ExtractionError: If *url*'s scheme isn't ``http``/``https``, or its
            host is ``localhost`` or a blocked address literal in canonical
            form.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ExtractionError(f"Blocked fetch to unsupported scheme: {url!r}")
    if not parsed.hostname or _is_blocked_host(parsed.hostname):
        raise ExtractionError(f"Blocked fetch to local/internal address: {url!r}")


_AddrInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[Any, ...]]


async def _getaddrinfo(host: str, port: int) -> list[_AddrInfo]:
    """Resolve *host* via the platform resolver. Isolated for test injection.

    Args:
        host: Hostname or address literal.
        port: Port to resolve for (the result doesn't vary with the port
            for TCP lookups, but ``getaddrinfo`` requires one).

    Returns:
        The raw ``socket.getaddrinfo`` result list.
    """
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)


async def _resolve_pinned_addresses(hostname: str, port: int) -> list[str]:
    """Resolve *hostname* and return every address literal safe to connect to.

    Every address the resolver returns is checked, and all of them
    rejected as a group if any one is internal — a multi-answer response
    mixing a public and an internal address is treated as fully blocked
    rather than letting the public answer stand in as an excuse to ignore
    the internal one. The resolver is consulted exactly once per call; the
    caller pins its connection to addresses from this list only, never
    re-resolving, which is what closes DNS rebinding (a hostname resolving
    differently between a check and the connection made after it). All
    validated addresses are returned, in resolver order, rather than only
    the first — a connection attempt against the first can legitimately
    fail for reasons that have nothing to do with SSRF (e.g. no outbound
    IPv6 route), and the caller falls back through the rest exactly the
    way an unpinned client's own address selection would.

    Args:
        hostname: A URL's hostname component.
        port: Port the caller is about to connect to.

    Returns:
        Every resolved address literal (``str``), in resolver order.

    Raises:
        ExtractionError: If resolution fails, or any candidate address is
            loopback, link-local, private, reserved, unspecified, or
            multicast.
    """
    try:
        infos = await _getaddrinfo(hostname, port)
    except OSError as exc:
        raise ExtractionError(f"Could not resolve host: {hostname!r} ({exc})") from exc

    resolved: list[str] = []
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_address(ip):
            raise ExtractionError(
                f"Blocked fetch to local/internal address: "
                f"{hostname!r} resolves to {ip}"
            )
        resolved.append(str(ip))

    if not resolved:
        raise ExtractionError(f"Could not resolve host: {hostname!r}")
    return resolved


def _bracket_if_ipv6(host: str) -> str:
    """Return *host* bracketed if it's an IPv6 literal, unchanged otherwise."""
    return f"[{host}]" if ":" in host else host


def _pinned_url(parsed: SplitResult, address: str, port: int) -> str:
    """Return *parsed* with its authority replaced by *address*:*port*.

    Args:
        parsed: The original URL, already split.
        address: The validated address to connect to.
        port: Port to carry into the pinned URL's authority.

    Returns:
        A URL string identical to the original except its authority is
        *address*:*port* instead of the original host (and any userinfo
        dropped) — this is the URL actually handed to the HTTP client, so
        it can never re-resolve the hostname itself.
    """
    netloc = f"{_bracket_if_ipv6(address)}:{port}"
    return urlunsplit(
        (parsed.scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment)
    )


def _host_header(hostname: str, port: int, *, explicit_port: bool) -> str:
    """Return the ``Host`` header value for the original *hostname*/*port*.

    Args:
        hostname: The original URL's hostname (unbracketed).
        port: The port to include if *explicit_port*.
        explicit_port: Whether the original URL specified a port — a
            default-port URL gets a bare-hostname ``Host`` header, matching
            standard HTTP convention.

    Returns:
        ``Host`` header value, bracketed if *hostname* is an IPv6 literal.
    """
    host = _bracket_if_ipv6(hostname)
    return f"{host}:{port}" if explicit_port else host


async def fetch_pinned_once(
    client: httpx2.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> httpx2.Response:
    """Issue one request to *url*, connecting to a freshly resolved, validated address.

    Does not follow redirects — a caller that wants to inspect a
    ``Location`` header itself (``social.py``) can read it off the returned
    response; a caller that wants automatic, SSRF-safe redirect following
    should use ``fetch_safely`` instead, which re-resolves and re-pins at
    every hop.

    Args:
        client: Shared ``httpx2.AsyncClient`` instance.
        method: HTTP method (``"GET"``, ``"HEAD"``, ...).
        url: The URL to fetch.
        headers: Extra headers for this request only, merged over the
            client's defaults. A ``Host`` header set here is overridden —
            the pinned request always sends the original hostname as
            ``Host``, never the address it's actually connecting to.

    Returns:
        The response. ``response.url`` reflects the pinned *address* URL,
        not *url* — callers that need the hostname-based URL (e.g. as a
        base for resolving relative links) must track *url* themselves
        rather than reading it off the response.

    Raises:
        ExtractionError: If *url*'s scheme isn't ``http``/``https``, its
            host can't be resolved, or any resolved address is loopback,
            link-local, private, reserved, unspecified, or multicast.
        httpx2.ConnectError: If every resolved address fails to connect.
        httpx2.ConnectTimeout: If every resolved address times out
            connecting (e.g. a VPN or firewall that drops rather than
            rejects packets to an unreachable address family — a sibling
            of ``ConnectError`` under ``TransportError``, not a subclass
            of it, so it needs its own ``except`` clause).
    """
    parsed = urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ExtractionError(f"Blocked fetch to unsupported scheme: {url!r}")
    hostname = parsed.hostname
    if not hostname:
        raise ExtractionError(f"Blocked fetch to local/internal address: {url!r}")

    port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    addresses = await _resolve_pinned_addresses(hostname, port)

    req_headers: dict[str, str] = dict(headers or {})
    req_headers["Host"] = _host_header(
        hostname, port, explicit_port=parsed.port is not None
    )
    extensions = {"sni_hostname": hostname} if parsed.scheme == "https" else {}

    last_error: httpx2.ConnectError | httpx2.ConnectTimeout | None = None
    for address in addresses:
        try:
            return await client.request(
                method,
                _pinned_url(parsed, address, port),
                headers=req_headers,
                extensions=extensions,
            )
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as exc:
            last_error = exc
    assert last_error is not None  # addresses is non-empty
    raise last_error


async def fetch_safely(
    client: httpx2.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    max_redirects: int = _MAX_REDIRECTS,
) -> tuple[httpx2.Response, str]:
    """Fetch *url*, following redirects, resolving and pinning at every hop.

    Each hop — the initial request and every redirect target — is
    independently resolved and validated via ``fetch_pinned_once``, so
    neither a redirect nor a DNS answer that changes between hops can
    retarget the connection at an internal address. A relative ``Location``
    is resolved against the hostname-based URL of the *current* hop, never
    against the pinned-address URL actually used for the connection.
    Redirects reuse *method* unchanged — every caller here only ever fetches
    via GET or HEAD, so the method-downgrade-on-redirect some HTTP clients
    apply for 302/303 never applies in practice.

    Args:
        client: Shared ``httpx2.AsyncClient`` instance.
        method: HTTP method for every hop.
        url: The URL to fetch.
        headers: Extra headers, forwarded to every hop.
        max_redirects: Safety cap on chain length.

    Returns:
        ``(response, final_url)`` — *final_url* is the hostname-based URL of
        the final hop (never a pinned-address URL), for use as a base for
        resolving relative links or as the canonical fetched URL.

    Raises:
        ExtractionError: If any hop targets a blocked scheme/host, or the
            chain exceeds *max_redirects*.
    """
    current = url
    for _ in range(max_redirects):
        response = await fetch_pinned_once(client, method, current, headers=headers)
        if response.status_code not in _REDIRECT_STATUS_CODES:
            return response, current
        location = response.headers.get("location")
        if not location:
            return response, current
        current = urljoin(current, location)
    raise ExtractionError(f"Too many redirects while fetching {url!r}")
