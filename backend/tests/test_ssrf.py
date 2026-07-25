from urllib.parse import urlsplit

import httpx2
import pytest

from analecta.extraction.core import ExtractionError
from analecta.extraction.ssrf import (
    _host_header,
    _pinned_url,
    fetch_pinned_once,
    fetch_safely,
    validate_fetch_url,
)

# mock_getaddrinfo fixture (tests/conftest.py) fakes ssrf._getaddrinfo
# per-hostname, falling back to the real resolver for anything not in the
# mapping — hermetic for made-up hostnames while still exercising the real
# resolver's own numeric-literal parsing for IP-literal hosts, which is
# exactly what closes the alternate-encoding bypass tested below.

# ---------------------------------------------------------------------------
# validate_fetch_url — cheap, non-authoritative pre-filter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "http://example.com/article",
        "https://8.8.8.8/path",
        "https://[2001:4860:4860::8888]/path",
        "https://[64:ff9b::808:808]/path",  # NAT64-embedded 8.8.8.8, public
        "https://[::808:808]/path",  # IPv4-compatible-embedded 8.8.8.8, public
    ],
)
def test_validate_fetch_url_allows_public_targets(url):
    validate_fetch_url(url)


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"]
)
def test_validate_fetch_url_blocks_non_http_schemes(url):
    with pytest.raises(ExtractionError):
        validate_fetch_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://127.255.255.255/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.1.1/",
        "http://[::ffff:169.254.1.1]/",
        "http://10.0.0.1/",
        "http://10.255.255.255/",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
        "http://192.168.1.1/",
        "http://192.168.255.255/",
        "http://0.0.0.0/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://224.0.0.1/",
        "http://[ff02::1]/",  # IPv6 multicast — is_global is True, is_multicast isn't
        "http://100.64.0.1/",  # CGNAT (RFC 6598) — not RFC 1918
        "http://198.18.0.1/",  # benchmarking (RFC 2544)
        "http://203.0.113.1/",  # TEST-NET-3 (RFC 5737)
        "http://[64:ff9b::7f00:1]/",  # NAT64-embedded 127.0.0.1
        "http://[64:ff9b::a00:1]/",  # NAT64-embedded 10.0.0.1
        "http://[::127.0.0.1]/",  # deprecated IPv4-compatible 127.0.0.1
        "http://[::10.0.0.1]/",  # deprecated IPv4-compatible 10.0.0.1
    ],
)
def test_validate_fetch_url_blocks_internal_hosts(url):
    with pytest.raises(ExtractionError):
        validate_fetch_url(url)


@pytest.mark.parametrize(
    "url",
    [
        # Just outside the RFC 1918 172.16.0.0/12 range on either edge.
        "http://172.15.255.255/",
        "http://172.32.0.0/",
    ],
)
def test_validate_fetch_url_allows_addresses_just_outside_blocked_ranges(url):
    validate_fetch_url(url)


# ---------------------------------------------------------------------------
# fetch_pinned_once / fetch_safely — the authoritative, resolution-based gate
#
# validate_fetch_url only recognizes a blocked host already in canonical
# ipaddress form. The real security boundary is here: every fetch resolves
# the host itself and validates the *resolved* address, then connects
# directly to it — so a hostname string can't out-vote the resolver, and a
# DNS answer that changes after the check can't retarget the connection.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_pinned_once_connects_to_resolved_address_not_hostname(
    mock_getaddrinfo,
):
    """The transport must see the resolved IP as connection target, the
    original hostname as Host header and TLS SNI — never the reverse."""
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    captured: dict[str, httpx2.Request] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["request"] = request
        return httpx2.Response(200, text="ok")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        response = await fetch_pinned_once(client, "GET", "https://example.com/path")

    assert response.status_code == 200
    request = captured["request"]
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "example.com"
    assert request.extensions.get("sni_hostname") == "example.com"


@pytest.mark.asyncio
async def test_fetch_pinned_once_host_header_includes_explicit_port(mock_getaddrinfo):
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    captured: dict[str, httpx2.Request] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["request"] = request
        return httpx2.Response(200, text="ok")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        await fetch_pinned_once(client, "GET", "https://example.com:8443/path")

    assert captured["request"].headers["host"] == "example.com:8443"


@pytest.mark.asyncio
async def test_fetch_pinned_once_blocks_non_http_scheme():
    async with httpx2.AsyncClient() as client:
        with pytest.raises(ExtractionError):
            await fetch_pinned_once(client, "GET", "file:///etc/passwd")


@pytest.mark.asyncio
async def test_fetch_pinned_once_falls_back_to_next_address_on_connect_failure(
    mock_getaddrinfo,
):
    """A dual-stack host where the first resolved address (e.g. an IPv6
    answer with no outbound route — a real, non-adversarial failure mode)
    must still succeed via a later address. Pinning to a single resolved
    address must not regress ordinary multi-homed connectivity that an
    unpinned client would have handled by trying the next answer."""
    mock_getaddrinfo({"example.com": ["2606:4700:10::6814:179a", "93.184.216.34"]})

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "2606:4700:10::6814:179a":
            raise httpx2.ConnectError("no route to host")
        return httpx2.Response(200, text="ok")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        response = await fetch_pinned_once(client, "GET", "https://example.com/path")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_fetch_pinned_once_falls_back_on_connect_timeout(mock_getaddrinfo):
    """Same as the ConnectError fallback, but for a dropped-not-rejected
    address (e.g. a VPN or firewall that blackholes an unreachable address
    family instead of refusing it) — httpx2.ConnectTimeout is a sibling of
    ConnectError under TransportError, not a subclass, so it needs its own
    coverage: a fix that only catches ConnectError silently misses this,
    the more common real-world manifestation of the same failure mode."""
    mock_getaddrinfo({"example.com": ["2606:4700:10::6814:179a", "93.184.216.34"]})

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "2606:4700:10::6814:179a":
            raise httpx2.ConnectTimeout("timed out")
        return httpx2.Response(200, text="ok")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        response = await fetch_pinned_once(client, "GET", "https://example.com/path")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_fetch_pinned_once_raises_when_every_address_fails_to_connect(
    mock_getaddrinfo,
):
    mock_getaddrinfo({"example.com": ["93.184.216.34", "93.184.216.35"]})

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx2.ConnectError):
            await fetch_pinned_once(client, "GET", "https://example.com/path")


@pytest.mark.asyncio
async def test_fetch_pinned_once_does_not_follow_redirects(mocker, mock_getaddrinfo):
    """social.py relies on this to inspect a Location header itself."""
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    handler = mocker.Mock(
        return_value=httpx2.Response(
            302, headers={"location": "https://example.com/next"}
        )
    )
    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        response = await fetch_pinned_once(client, "GET", "https://example.com/start")

    assert response.status_code == 302
    assert handler.call_count == 1


@pytest.mark.asyncio
async def test_fetch_safely_blocks_hostname_resolving_to_internal_address(
    mocker, mock_getaddrinfo
):
    """A DNS name isn't exempt just because it isn't an IP literal — the
    resolved address is what's validated, regardless of what the hostname
    string looks like."""
    mock_getaddrinfo({"attacker-controlled.example": ["127.0.0.1"]})
    handler = mocker.Mock(return_value=httpx2.Response(200, text="ok"))
    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(ExtractionError):
            await fetch_safely(
                client, "GET", "https://attacker-controlled.example/x.png"
            )
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_safely_blocks_when_any_resolved_address_is_internal(
    mocker, mock_getaddrinfo
):
    """Reject-on-any: one internal answer among several is enough to block,
    even if another answer for the same host is public."""
    mock_getaddrinfo({"multi-answer.example": ["93.184.216.34", "127.0.0.1"]})
    handler = mocker.Mock(return_value=httpx2.Response(200, text="ok"))
    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(ExtractionError):
            await fetch_safely(client, "GET", "https://multi-answer.example/x.png")
    handler.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/admin",  # bare decimal for 127.0.0.1
        "http://0x7f000001/admin",  # hex for 127.0.0.1
        "http://017700000001/admin",  # legacy octal for 127.0.0.1
        "http://127.1/admin",  # short-dotted for 127.0.0.1
        "http://localhost./admin",  # trailing-dot localhost
    ],
)
async def test_fetch_safely_blocks_alternate_loopback_encodings(url, mocker):
    """validate_fetch_url's ipaddress-based string check doesn't parse any
    of these, but the platform resolver used to actually open the
    connection does — resolving via that same resolver (rather than
    pattern-matching the string) is what closes the bypass. No
    mock_getaddrinfo here: these must go through the real resolver."""
    handler = mocker.Mock(return_value=httpx2.Response(200, text="ok"))
    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(ExtractionError):
            await fetch_safely(client, "GET", url)
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_safely_blocks_redirect_to_internal_host(mock_getaddrinfo):
    mock_getaddrinfo({"start.example": ["93.184.216.34"]})

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "http://127.0.0.1/admin"})

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(ExtractionError):
            await fetch_safely(client, "GET", "https://start.example/begin")


@pytest.mark.asyncio
async def test_fetch_safely_follows_redirect_to_public_host(mock_getaddrinfo):
    mock_getaddrinfo(
        {"start.example": ["93.184.216.34"], "next.example": ["93.184.216.35"]},
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("host") == "start.example":
            return httpx2.Response(
                302, headers={"location": "https://next.example/final"}
            )
        return httpx2.Response(200, text="ok")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        response, final_url = await fetch_safely(
            client, "GET", "https://start.example/begin"
        )

    assert response.status_code == 200
    assert final_url == "https://next.example/final"


@pytest.mark.asyncio
async def test_fetch_safely_resolves_relative_redirect_against_hostname_url(
    mock_getaddrinfo,
):
    """Regression: a relative Location must be joined against the
    hostname-based URL of the current hop, never the pinned-address URL
    actually sent to the transport — joining against the IP URL would
    silently corrupt the target."""
    mock_getaddrinfo({"start.example": ["93.184.216.34"]})

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/begin":
            return httpx2.Response(302, headers={"location": "/final"})
        return httpx2.Response(200, text="ok")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        response, final_url = await fetch_safely(
            client, "GET", "https://start.example/begin"
        )

    assert response.status_code == 200
    assert final_url == "https://start.example/final"


@pytest.mark.asyncio
async def test_fetch_safely_raises_after_too_many_redirects(mock_getaddrinfo):
    mock_getaddrinfo({"loop.example": ["93.184.216.34"]})

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "https://loop.example/begin"})

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(ExtractionError, match="Too many redirects"):
            await fetch_safely(
                client, "GET", "https://loop.example/begin", max_redirects=3
            )


# ---------------------------------------------------------------------------
# Pinned URL / Host header construction — IPv6 bracketing
# ---------------------------------------------------------------------------


def test_pinned_url_brackets_ipv6_address():
    parsed = urlsplit("https://example.com/path?q=1")
    assert (
        _pinned_url(parsed, "2001:db8::1", 443) == "https://[2001:db8::1]:443/path?q=1"
    )


def test_host_header_brackets_ipv6_hostname():
    assert _host_header("2001:db8::1", 443, explicit_port=False) == "[2001:db8::1]"
    assert _host_header("2001:db8::1", 8443, explicit_port=True) == "[2001:db8::1]:8443"
    assert _host_header("example.com", 80, explicit_port=False) == "example.com"
