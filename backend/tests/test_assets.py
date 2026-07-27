import asyncio
import hashlib

import httpx2
import pytest

import analecta.extraction.assets as assets_module
from analecta.extraction.assets import (
    AssetDownloader,
    _ext_from_content_type,
    _ext_from_url,
    _normalize_graphics,
    _original_name,
    _placeholder_bytes,
    _placeholder_filename,
    _resolve_nextjs_image,
)


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """Zero out the real retry delay so failure-path tests don't sleep for real."""
    monkeypatch.setattr(assets_module, "_RETRY_DELAY_SECONDS", 0)


_HTML_WITH_IMAGES = (
    "<html><body>"
    '<img src="https://example.com/photo.jpg" alt="A photo">'
    '<img src="https://example.com/logo.png">'
    "</body></html>"
)
_HTML_NO_IMAGES = "<html><body><p>No images.</p></body></html>"
_HTML_DATA_URI = '<img src="data:image/png;base64,abc123">'
_HTML_DUPLICATE = (
    '<img src="https://example.com/same.jpg"><img src="https://example.com/same.jpg">'
)

# ---------------------------------------------------------------------------
# _ext_from_content_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ct", "expected"),
    [
        ("image/jpeg", ".jpg"),
        ("image/png", ".png"),
        ("image/webp", ".webp"),
        ("image/svg+xml", ".svg"),
        ("image/avif", ".avif"),
        ("image/jpeg; charset=utf-8", ".jpg"),
        ("text/html", ""),
        ("application/octet-stream", ""),
        ("", ""),
    ],
)
def test_ext_from_content_type(ct, expected):
    assert _ext_from_content_type(ct) == expected


# ---------------------------------------------------------------------------
# _ext_from_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://cdn.example.com/img/photo.jpg", ".jpg"),
        ("https://cdn.example.com/img/logo.PNG", ".png"),
        ("https://example.com/noop?v=1", ""),
        ("https://example.com/image.exe", ""),
    ],
)
def test_ext_from_url(url, expected):
    assert _ext_from_url(url) == expected


# ---------------------------------------------------------------------------
# _original_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/path/photo.jpg", "photo.jpg"),
        ("https://example.com/logo.svg", "logo.svg"),
        ("https://example.com/", "image"),
        ("https://example.com", "image"),
    ],
)
def test_original_name(url, expected):
    assert _original_name(url) == expected


# ---------------------------------------------------------------------------
# _resolve_nextjs_image
# ---------------------------------------------------------------------------


def test_resolve_nextjs_image_query_param():
    src = "/_next/image?url=https%3A%2F%2Fcdn.example.com%2Fphoto.jpg&w=1080&q=75"
    assert _resolve_nextjs_image(src) == "https://cdn.example.com/photo.jpg"


def test_resolve_nextjs_image_passthrough():
    src = "https://cdn.example.com/photo.jpg"
    assert _resolve_nextjs_image(src) == src


def test_resolve_nextjs_image_no_url_param():
    src = "/_next/image?w=1080"
    assert _resolve_nextjs_image(src) == src


# ---------------------------------------------------------------------------
# _normalize_graphics
# ---------------------------------------------------------------------------


def test_normalize_graphics_promotes_data_src():
    html = (
        '<img src="data:image/gif;base64,R0lGOD"'
        ' data-src="https://cdn.example.com/real.jpg" alt="photo">'
    )
    result = _normalize_graphics(html)
    assert 'src="https://cdn.example.com/real.jpg"' in result


def test_normalize_graphics_skips_real_src():
    html = '<img src="https://cdn.example.com/photo.jpg" data-src="https://cdn.example.com/other.jpg">'
    result = _normalize_graphics(html)
    assert 'src="https://cdn.example.com/photo.jpg"' in result


def test_normalize_graphics_resolves_nextjs_proxy():
    encoded = "https%3A%2F%2Fcdn.example.com%2Fphoto.jpg"
    html = f'<img src="/_next/image?url={encoded}&w=1080&q=75" alt="photo">'
    result = _normalize_graphics(html)
    assert 'src="https://cdn.example.com/photo.jpg"' in result


def test_normalize_graphics_converts_graphic_elements():
    html = '<graphic src="https://cdn.example.com/img.png" alt="caption"/>'
    result = _normalize_graphics(html)
    assert "<img" in result
    assert 'src="https://cdn.example.com/img.png"' in result


# ---------------------------------------------------------------------------
# AssetDownloader._discover_images
# ---------------------------------------------------------------------------


def test_discover_images_finds_srcs():
    urls = AssetDownloader()._discover_images(_HTML_WITH_IMAGES)
    assert "https://example.com/photo.jpg" in urls
    assert "https://example.com/logo.png" in urls


def test_discover_images_skips_data_uris():
    assert AssetDownloader()._discover_images(_HTML_DATA_URI) == []


def test_discover_images_deduplicates():
    urls = AssetDownloader()._discover_images(_HTML_DUPLICATE)
    assert urls == ["https://example.com/same.jpg"]


def test_discover_images_empty_html():
    assert AssetDownloader()._discover_images(_HTML_NO_IMAGES) == []


# ---------------------------------------------------------------------------
# AssetDownloader._download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_saves_with_sha256_name(mocker, mock_getaddrinfo, tmp_path):
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    data = b"fake-image-bytes"
    sha = hashlib.sha256(data).hexdigest()

    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = mocker.Mock()
    mock_resp.headers = {"content-type": "image/png"}
    mock_resp.content = data

    mock_client = mocker.AsyncMock()
    mock_client.request = mocker.AsyncMock(return_value=mock_resp)

    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://example.com/img.png", asset_dir, mock_client, asyncio.Semaphore(1)
    )

    assert result == f"{sha[:16]}.png"
    assert (asset_dir / result).read_bytes() == data


@pytest.mark.asyncio
async def test_download_falls_back_to_placeholder_on_non_image_content_type(
    mocker, mock_getaddrinfo, tmp_path
):
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = mocker.Mock()
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.content = b"<html></html>"

    mock_client = mocker.AsyncMock()
    mock_client.request = mocker.AsyncMock(return_value=mock_resp)

    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://example.com/page", asset_dir, mock_client, asyncio.Semaphore(1)
    )
    assert result == _placeholder_filename()
    assert (asset_dir / result).read_bytes() == _placeholder_bytes()
    assert mock_client.request.call_count == 2


@pytest.mark.asyncio
async def test_download_falls_back_to_placeholder_on_network_error(
    mocker, mock_getaddrinfo, tmp_path
):
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    mock_client = mocker.AsyncMock()
    mock_client.request = mocker.AsyncMock(side_effect=Exception("network error"))

    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://example.com/img.jpg", asset_dir, mock_client, asyncio.Semaphore(1)
    )
    assert result == _placeholder_filename()
    assert (asset_dir / result).read_bytes() == _placeholder_bytes()
    assert mock_client.request.call_count == 2


@pytest.mark.asyncio
async def test_download_recovers_on_retry_after_transient_failure(
    mocker, mock_getaddrinfo, tmp_path
):
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    data = b"fake-image-bytes"
    sha = hashlib.sha256(data).hexdigest()

    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = mocker.Mock()
    mock_resp.headers = {"content-type": "image/png"}
    mock_resp.content = data

    mock_client = mocker.AsyncMock()
    mock_client.request = mocker.AsyncMock(
        side_effect=[Exception("transient"), mock_resp]
    )

    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://example.com/img.png", asset_dir, mock_client, asyncio.Semaphore(1)
    )

    assert result == f"{sha[:16]}.png"
    assert (asset_dir / result).read_bytes() == data
    assert mock_client.request.call_count == 2


# ---------------------------------------------------------------------------
# AssetDownloader._placeholder
# ---------------------------------------------------------------------------


def test_placeholder_writes_file_and_returns_filename(tmp_path):
    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    filename = AssetDownloader()._placeholder(asset_dir)

    assert filename == _placeholder_filename()
    assert (asset_dir / filename).read_bytes() == _placeholder_bytes()


def test_placeholder_is_idempotent(tmp_path):
    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    first = AssetDownloader()._placeholder(asset_dir)
    second = AssetDownloader()._placeholder(asset_dir)

    assert first == second
    assert (asset_dir / first).read_bytes() == _placeholder_bytes()


def test_placeholder_shared_filename_across_asset_dirs(tmp_path):
    dir_a = tmp_path / "assets" / "slug-a"
    dir_b = tmp_path / "assets" / "slug-b"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)

    name_a = AssetDownloader()._placeholder(dir_a)
    name_b = AssetDownloader()._placeholder(dir_b)

    assert name_a == name_b


@pytest.mark.asyncio
async def test_download_falls_back_to_url_extension(mocker, mock_getaddrinfo, tmp_path):
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    data = b"svg-data"
    sha = hashlib.sha256(data).hexdigest()

    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = mocker.Mock()
    mock_resp.headers = {"content-type": "image/svg+xml"}
    mock_resp.content = data

    mock_client = mocker.AsyncMock()
    mock_client.request = mocker.AsyncMock(return_value=mock_resp)

    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://example.com/icon.svg", asset_dir, mock_client, asyncio.Semaphore(1)
    )
    assert result == f"{sha[:16]}.svg"


# ---------------------------------------------------------------------------
# AssetDownloader.process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_unchanged_when_no_images(tmp_path):
    result = await AssetDownloader().process(_HTML_NO_IMAGES, "slug", tmp_path)
    assert result == _HTML_NO_IMAGES


@pytest.mark.asyncio
async def test_process_rewrites_src_to_local_path(mocker, tmp_path):
    filename = "abc123def45678.png"
    mocker.patch.object(AssetDownloader, "_download", return_value=filename)

    html = '<img src="https://example.com/logo.png">'
    result = await AssetDownloader().process(html, "my-slug", tmp_path)

    assert f'src="../assets/my-slug/{filename}"' in result


@pytest.mark.asyncio
async def test_process_rewrites_src_to_placeholder_on_download_failure(
    mocker, tmp_path
):
    mocker.patch.object(
        AssetDownloader, "_download", return_value=_placeholder_filename()
    )

    html = '<img src="https://example.com/photo.jpg">'
    result = await AssetDownloader().process(html, "my-slug", tmp_path)

    assert f'src="../assets/my-slug/{_placeholder_filename()}"' in result
    assert "https://example.com/photo.jpg" not in result


@pytest.mark.asyncio
async def test_process_injects_alt_for_tag_without_alt(mocker, tmp_path):
    mocker.patch.object(AssetDownloader, "_download", return_value="abc123.jpg")

    html = '<img src="https://example.com/photo.jpg">'
    result = await AssetDownloader().process(html, "slug", tmp_path)

    assert 'alt="photo.jpg"' in result


@pytest.mark.asyncio
async def test_process_preserves_existing_alt(mocker, tmp_path):
    mocker.patch.object(AssetDownloader, "_download", return_value="abc123.jpg")

    html = '<img src="https://example.com/photo.jpg" alt="My Photo">'
    result = await AssetDownloader().process(html, "slug", tmp_path)

    assert 'alt="My Photo"' in result
    assert result.count("alt=") == 1


@pytest.mark.asyncio
async def test_process_creates_asset_directory(mocker, tmp_path):
    mocker.patch.object(AssetDownloader, "_download", return_value="abc123.png")

    html = '<img src="https://example.com/img.png">'
    await AssetDownloader().process(html, "entry-slug", tmp_path)

    assert (tmp_path / "assets" / "entry-slug").is_dir()


@pytest.mark.asyncio
async def test_process_downloads_through_real_client_with_redirect_hook(
    mocker, mock_getaddrinfo, tmp_path
):
    """Regression guard: the resolve/pin plumbing must not break an ordinary
    image download through the real AsyncClient — see ssrf.py."""
    mock_getaddrinfo({"example.com": ["93.184.216.34"]})
    png_bytes = b"\x89PNG\r\n\x1a\n fake"
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(
            200, headers={"content-type": "image/png"}, content=png_bytes
        )
    )
    real_client = httpx2.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    mocker.patch(
        "analecta.extraction.assets.httpx2.AsyncClient", side_effect=client_factory
    )

    html = '<img src="https://example.com/real.png">'
    result = await AssetDownloader().process(html, "slug", tmp_path)

    sha = hashlib.sha256(png_bytes).hexdigest()
    assert f"../assets/slug/{sha[:16]}.png" in result


@pytest.mark.asyncio
async def test_process_blocks_image_url_targeting_internal_host(mocker, tmp_path):
    """An <img src> pointing at an internal host — attacker-controlled, since
    it comes from already-fetched page content, not the pasted URL — must
    degrade to the placeholder without ever reaching the transport."""
    handler = mocker.Mock(
        return_value=httpx2.Response(
            200, headers={"content-type": "image/png"}, content=b"would-have-worked"
        )
    )
    transport = httpx2.MockTransport(handler)
    real_client = httpx2.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    mocker.patch(
        "analecta.extraction.assets.httpx2.AsyncClient", side_effect=client_factory
    )

    html = '<img src="http://127.0.0.1/internal.png">'
    result = await AssetDownloader().process(html, "slug", tmp_path)

    assert f"../assets/slug/{_placeholder_filename()}" in result
    handler.assert_not_called()


# ---------------------------------------------------------------------------
# AssetDownloader.process — base_url resolution of relative img srcs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_resolves_root_relative_src_with_base_url(mocker, tmp_path):
    filename = "abc123def45678.svg"
    mock_download = mocker.patch.object(
        AssetDownloader, "_download", return_value=filename
    )

    html = '<img src="/shared-assets/images/diagrams/position-area.svg">'
    result = await AssetDownloader().process(
        html,
        "my-slug",
        tmp_path,
        base_url="https://developer.mozilla.org/en-US/docs/Web/CSS/anchor",
    )

    called_url = mock_download.call_args.args[0]
    assert (
        called_url
        == "https://developer.mozilla.org/shared-assets/images/diagrams/position-area.svg"
    )
    assert f'src="../assets/my-slug/{filename}"' in result


@pytest.mark.asyncio
async def test_process_resolves_protocol_relative_src_with_base_url(mocker, tmp_path):
    filename = "abc123def45678.png"
    mock_download = mocker.patch.object(
        AssetDownloader, "_download", return_value=filename
    )

    html = '<img src="//cdn.example.com/logo.png">'
    result = await AssetDownloader().process(
        html, "slug", tmp_path, base_url="https://example.com/article"
    )

    called_url = mock_download.call_args.args[0]
    assert called_url == "https://cdn.example.com/logo.png"
    assert f'src="../assets/slug/{filename}"' in result


@pytest.mark.asyncio
async def test_process_leaves_absolute_src_unchanged_with_base_url(mocker, tmp_path):
    filename = "abc123def45678.jpg"
    mock_download = mocker.patch.object(
        AssetDownloader, "_download", return_value=filename
    )

    html = '<img src="https://other-cdn.example.com/photo.jpg">'
    result = await AssetDownloader().process(
        html, "slug", tmp_path, base_url="https://example.com/article"
    )

    called_url = mock_download.call_args.args[0]
    assert called_url == "https://other-cdn.example.com/photo.jpg"
    assert f'src="../assets/slug/{filename}"' in result


@pytest.mark.asyncio
async def test_process_leaves_relative_src_unresolved_without_base_url(
    mocker, tmp_path
):
    mock_download = mocker.patch.object(AssetDownloader, "_download", return_value=None)

    html = '<img src="/shared-assets/images/diagram.svg">'
    result = await AssetDownloader().process(html, "slug", tmp_path)

    called_url = mock_download.call_args.args[0]
    assert called_url == "/shared-assets/images/diagram.svg"
    assert 'src="/shared-assets/images/diagram.svg"' in result


# ---------------------------------------------------------------------------
# AssetDownloader.localize_markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_localize_markdown_noop_without_remote_refs(mocker, tmp_path):
    mock_download = mocker.patch.object(AssetDownloader, "_download")

    markdown = "# Title\n\n![local](../assets/slug/abc123.png)\n\nSome text.\n"
    result, changed, placeholders = await AssetDownloader().localize_markdown(
        markdown, "slug", tmp_path
    )

    assert result == markdown
    assert changed is False
    assert placeholders == 0
    mock_download.assert_not_called()


@pytest.mark.asyncio
async def test_localize_markdown_rewrites_absolute_url(mocker, tmp_path):
    filename = "abc123def45678.png"
    mocker.patch.object(AssetDownloader, "_download", return_value=filename)

    markdown = "![a photo](https://example.com/photo.png)\n"
    result, changed, placeholders = await AssetDownloader().localize_markdown(
        markdown, "my-slug", tmp_path
    )

    assert result == f"![a photo](../assets/my-slug/{filename})\n"
    assert changed is True
    assert placeholders == 0


@pytest.mark.asyncio
async def test_localize_markdown_resolves_protocol_relative_with_base_url(
    mocker, tmp_path
):
    filename = "abc123def45678.png"
    mock_download = mocker.patch.object(
        AssetDownloader, "_download", return_value=filename
    )

    markdown = "![logo](//upload.wikimedia.org/logo.png)\n"
    result, changed, _ = await AssetDownloader().localize_markdown(
        markdown, "slug", tmp_path, base_url="https://es.wikipedia.org/wiki/Foo"
    )

    called_url = mock_download.call_args.args[0]
    assert called_url == "https://upload.wikimedia.org/logo.png"
    assert f"../assets/slug/{filename}" in result
    assert changed is True


@pytest.mark.asyncio
async def test_localize_markdown_counts_placeholders(mocker, tmp_path):
    mocker.patch.object(
        AssetDownloader, "_download", return_value=_placeholder_filename()
    )

    markdown = "![gone](https://example.com/dead.png)\n"
    result, changed, placeholders = await AssetDownloader().localize_markdown(
        markdown, "slug", tmp_path
    )

    assert changed is True
    assert placeholders == 1
    assert _placeholder_filename() in result


@pytest.mark.asyncio
async def test_localize_markdown_dedupes_repeated_url(mocker, tmp_path):
    filename = "abc123def45678.png"
    mock_download = mocker.patch.object(
        AssetDownloader, "_download", return_value=filename
    )

    markdown = (
        "![first](https://example.com/same.png)\n\n"
        "![second](https://example.com/same.png)\n"
    )
    result, changed, _ = await AssetDownloader().localize_markdown(
        markdown, "slug", tmp_path
    )

    assert mock_download.call_count == 1
    assert result.count(f"../assets/slug/{filename}") == 2
    assert changed is True


@pytest.mark.asyncio
async def test_localize_markdown_unchanged_when_download_maps_nothing(mocker, tmp_path):
    # A None result (e.g. an exception return_exceptions=True swallowed) is
    # filtered out by localize_markdown's isinstance(filename, str) check —
    # the URL still matches the discovery regex, but nothing gets rewritten.
    mocker.patch.object(AssetDownloader, "_download", return_value=None)

    markdown = "![broken](https://example.com/broken.png)\n"
    result, changed, placeholders = await AssetDownloader().localize_markdown(
        markdown, "slug", tmp_path
    )

    assert result == markdown
    assert changed is False
    assert placeholders == 0


@pytest.mark.asyncio
async def test_localize_markdown_leaves_unmapped_url_unchanged(mocker, tmp_path):
    filename = "abc123def45678.png"

    async def fake_download(url, *_args, **_kwargs):
        return None if "unresolved" in url else filename

    mocker.patch.object(AssetDownloader, "_download", side_effect=fake_download)

    markdown = (
        "![ok](https://example.com/photo.png)\n\n"
        "![gone](https://example.com/unresolved.png)\n"
    )
    result, changed, _ = await AssetDownloader().localize_markdown(
        markdown, "slug", tmp_path
    )

    assert f"../assets/slug/{filename}" in result
    assert "https://example.com/unresolved.png" in result
    assert changed is True


@pytest.mark.asyncio
async def test_localize_markdown_creates_asset_directory(mocker, tmp_path):
    filename = "abc123def45678.png"
    mocker.patch.object(AssetDownloader, "_download", return_value=filename)

    markdown = "![a photo](https://example.com/photo.png)\n"
    await AssetDownloader().localize_markdown(markdown, "fresh-slug", tmp_path)

    assert (tmp_path / "assets" / "fresh-slug").is_dir()
