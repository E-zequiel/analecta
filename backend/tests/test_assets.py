import asyncio
import hashlib

import pytest

from analecta.extraction.assets import (
    AssetDownloader,
    _ext_from_content_type,
    _ext_from_url,
    _normalize_graphics,
    _original_name,
    _resolve_nextjs_image,
    _shot_id_from_url,
)

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
async def test_download_saves_with_sha256_name(mocker, tmp_path):
    data = b"fake-image-bytes"
    sha = hashlib.sha256(data).hexdigest()

    mock_resp = mocker.Mock()
    mock_resp.raise_for_status = mocker.Mock()
    mock_resp.headers = {"content-type": "image/png"}
    mock_resp.content = data

    mock_client = mocker.AsyncMock()
    mock_client.get = mocker.AsyncMock(return_value=mock_resp)

    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://example.com/img.png", asset_dir, mock_client, asyncio.Semaphore(1)
    )

    assert result == f"{sha[:16]}.png"
    assert (asset_dir / result).read_bytes() == data


@pytest.mark.asyncio
async def test_download_rejects_non_image_content_type(mocker, tmp_path):
    mock_resp = mocker.Mock()
    mock_resp.raise_for_status = mocker.Mock()
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.content = b"<html></html>"

    mock_client = mocker.AsyncMock()
    mock_client.get = mocker.AsyncMock(return_value=mock_resp)

    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://example.com/page", asset_dir, mock_client, asyncio.Semaphore(1)
    )
    assert result is None


@pytest.mark.asyncio
async def test_download_returns_none_on_network_error(mocker, tmp_path):
    mock_client = mocker.AsyncMock()
    mock_client.get = mocker.AsyncMock(side_effect=Exception("network error"))

    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://example.com/img.jpg", asset_dir, mock_client, asyncio.Semaphore(1)
    )
    assert result is None


@pytest.mark.asyncio
async def test_download_falls_back_to_url_extension(mocker, tmp_path):
    data = b"svg-data"
    sha = hashlib.sha256(data).hexdigest()

    mock_resp = mocker.Mock()
    mock_resp.raise_for_status = mocker.Mock()
    mock_resp.headers = {"content-type": "image/svg+xml"}
    mock_resp.content = data

    mock_client = mocker.AsyncMock()
    mock_client.get = mocker.AsyncMock(return_value=mock_resp)

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
async def test_process_fallback_preserves_original_url(mocker, tmp_path):
    mocker.patch.object(AssetDownloader, "_download", return_value=None)

    html = '<img src="https://example.com/photo.jpg">'
    result = await AssetDownloader().process(html, "my-slug", tmp_path)

    assert 'src="https://example.com/photo.jpg"' in result


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


# ---------------------------------------------------------------------------
# _shot_id_from_url
# ---------------------------------------------------------------------------


def test_shot_id_from_url_matches_placeholder():
    assert (
        _shot_id_from_url("https://analecta-shot.invalid/shot/shot-0.png") == "shot-0"
    )


def test_shot_id_from_url_returns_none_for_other_hosts():
    assert _shot_id_from_url("https://example.com/shot/shot-0.png") is None


def test_shot_id_from_url_returns_none_for_relative_url():
    assert _shot_id_from_url("/shot/shot-0.png") is None


# ---------------------------------------------------------------------------
# AssetDownloader._download — Tier-2 screenshot placeholder resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_resolves_shot_from_captured_images(mocker, tmp_path):
    data = b"fake-png-bytes"
    sha = hashlib.sha256(data).hexdigest()

    mock_client = mocker.AsyncMock()
    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://analecta-shot.invalid/shot/shot-0.png",
        asset_dir,
        mock_client,
        asyncio.Semaphore(1),
        {"shot-0": data},
    )

    assert result == f"{sha[:16]}.png"
    assert (asset_dir / result).read_bytes() == data
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_download_returns_none_for_unmapped_shot_id(mocker, tmp_path):
    mock_client = mocker.AsyncMock()
    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://analecta-shot.invalid/shot/shot-0.png",
        asset_dir,
        mock_client,
        asyncio.Semaphore(1),
        {"shot-1": b"other bytes"},
    )

    assert result is None
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_download_returns_none_for_shot_url_without_captured_images(
    mocker, tmp_path
):
    mock_client = mocker.AsyncMock()
    asset_dir = tmp_path / "assets" / "slug"
    asset_dir.mkdir(parents=True)

    result = await AssetDownloader()._download(
        "https://analecta-shot.invalid/shot/shot-0.png",
        asset_dir,
        mock_client,
        asyncio.Semaphore(1),
    )

    assert result is None
    mock_client.get.assert_not_called()


# ---------------------------------------------------------------------------
# AssetDownloader.process — Tier-2 screenshot placeholder resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_resolves_shot_placeholder_to_local_asset(tmp_path):
    data = b"fake-png-bytes"
    sha = hashlib.sha256(data).hexdigest()

    html = '<img src="https://analecta-shot.invalid/shot/shot-0.png" alt="demo">'
    result = await AssetDownloader().process(
        html, "my-slug", tmp_path, captured_images={"shot-0": data}
    )

    filename = f"{sha[:16]}.png"
    assert f'src="../assets/my-slug/{filename}"' in result
    assert (tmp_path / "assets" / "my-slug" / filename).read_bytes() == data


@pytest.mark.asyncio
async def test_process_leaves_shot_placeholder_unchanged_without_matching_bytes(
    tmp_path,
):
    html = '<img src="https://analecta-shot.invalid/shot/shot-0.png" alt="demo">'
    result = await AssetDownloader().process(html, "my-slug", tmp_path)

    assert 'src="https://analecta-shot.invalid/shot/shot-0.png"' in result


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
