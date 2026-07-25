import httpx2
import pytest

from analecta.extraction.core import ExtractionError
from analecta.extraction.ssrf import block_redirect_to_internal, validate_fetch_url

# ---------------------------------------------------------------------------
# validate_fetch_url — allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "http://example.com/article",
        "https://8.8.8.8/path",
        "https://[2001:4860:4860::8888]/path",
    ],
)
def test_validate_fetch_url_allows_public_targets(url):
    validate_fetch_url(url)


# ---------------------------------------------------------------------------
# validate_fetch_url — blocked scheme
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"]
)
def test_validate_fetch_url_blocks_non_http_schemes(url):
    with pytest.raises(ExtractionError):
        validate_fetch_url(url)


# ---------------------------------------------------------------------------
# validate_fetch_url — blocked hosts
# ---------------------------------------------------------------------------


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
# block_redirect_to_internal
# ---------------------------------------------------------------------------


def _redirect_response(start_url: str, location: str) -> httpx2.Response:
    return httpx2.Response(
        302,
        headers={"location": location},
        request=httpx2.Request("GET", start_url),
    )


@pytest.mark.asyncio
async def test_block_redirect_to_internal_noop_without_location_header():
    response = httpx2.Response(
        200, request=httpx2.Request("GET", "https://example.com")
    )
    await block_redirect_to_internal(response)  # must not raise


@pytest.mark.asyncio
async def test_block_redirect_to_internal_allows_redirect_to_public_host():
    response = _redirect_response(
        "https://example.com/start", "https://example.com/next"
    )
    await block_redirect_to_internal(response)  # must not raise


@pytest.mark.asyncio
async def test_block_redirect_to_internal_blocks_redirect_to_loopback():
    response = _redirect_response("https://example.com/start", "http://127.0.0.1/admin")
    with pytest.raises(ExtractionError):
        await block_redirect_to_internal(response)


@pytest.mark.asyncio
async def test_block_redirect_to_internal_blocks_relative_redirect_to_loopback():
    """A relative Location is resolved against response.url before checking."""
    response = _redirect_response("http://127.0.0.1/start", "/next")
    with pytest.raises(ExtractionError):
        await block_redirect_to_internal(response)


# ---------------------------------------------------------------------------
# block_redirect_to_internal — wired into a real AsyncClient
#
# httpx2.AsyncClient always does ``await hook(response)`` for every response,
# not just redirects — a hook that isn't itself ``async def`` breaks every
# response the hook is a no-op for (TypeError: NoneType can't be awaited),
# not just the blocked ones. Unit-testing the hook function in isolation
# (above) can't catch this; it requires exercising the real client.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_on_real_client_does_not_break_normal_response():
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, text="ok"))
    async with httpx2.AsyncClient(
        transport=transport,
        event_hooks={"response": [block_redirect_to_internal]},
    ) as client:
        response = await client.get("http://example.com/start")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_hook_on_real_client_follows_redirect_to_public_host():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/start":
            return httpx2.Response(302, headers={"location": "http://example.com/next"})
        return httpx2.Response(200, text="ok")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(
        transport=transport,
        follow_redirects=True,
        event_hooks={"response": [block_redirect_to_internal]},
    ) as client:
        response = await client.get("http://example.com/start")
    assert response.status_code == 200
    assert str(response.url) == "http://example.com/next"


@pytest.mark.asyncio
async def test_hook_on_real_client_blocks_redirect_to_internal_host():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/start":
            return httpx2.Response(302, headers={"location": "http://127.0.0.1/admin"})
        return httpx2.Response(200, text="ok")

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(
        transport=transport,
        follow_redirects=True,
        event_hooks={"response": [block_redirect_to_internal]},
    ) as client:
        with pytest.raises(ExtractionError):
            await client.get("http://example.com/start")
