"""Asset downloader — M3 pipeline."""

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

_HEADERS = {"User-Agent": "analecta/0.1.0 (+https://github.com/E-zequiel/analecta)"}
_TIMEOUT = 30.0
_MAX_CONCURRENT = 5

_IMG_TAG_RE = re.compile(r"<img\s[^>]+>", re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
_ALT_ATTR_RE = re.compile(r"\balt=", re.IGNORECASE)

_CONTENT_TYPE_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/avif": ".avif",
}


def _ext_from_content_type(content_type: str) -> str:
    """Return file extension for *content_type*, or ``''`` if unrecognised.

    Args:
        content_type: Raw ``Content-Type`` header value (may include parameters).

    Returns:
        Extension string including the leading dot (e.g. ``'.jpg'``), or ``''``.
    """
    mime = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXT.get(mime, "")


def _ext_from_url(url: str) -> str:
    """Return the file extension from *url*'s path component, or ``''``.

    Args:
        url: Absolute URL of an image.

    Returns:
        Lowercase extension (e.g. ``'.png'``) if it maps to a known image type,
        otherwise ``''``.
    """
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in _CONTENT_TYPE_EXT.values() else ""


def _original_name(url: str) -> str:
    """Return the bare filename from *url*, falling back to ``'image'``.

    Args:
        url: Absolute URL.

    Returns:
        Filename component (e.g. ``'logo.png'``), or ``'image'`` if the path
        has no filename.
    """
    name = Path(urlparse(url).path).name
    return name or "image"


class AssetDownloader:
    """Downloads images referenced in extracted HTML and rewrites src paths.

    Saved as ``{vault}/assets/{slug}/{sha256[:16]}.{ext}`` (content-addressed).
    Non-image responses and network failures are silently skipped — the original
    URL is kept so the pipeline never breaks.
    """

    async def process(self, html: str, slug: str, vault_path: Path) -> str:
        """Download images in *html*, rewrite src attrs, return modified HTML.

        Args:
            html: Extracted HTML (M2 output).
            slug: Entry slug used as the asset subdirectory name.
            vault_path: Root vault directory.

        Returns:
            HTML with ``../assets/{slug}/...`` paths replacing remote src URLs
            for successfully downloaded images.
        """
        urls = self._discover_images(html)
        if not urls:
            return html

        asset_dir = vault_path / "assets" / slug
        asset_dir.mkdir(parents=True, exist_ok=True)

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_TIMEOUT, headers=_HEADERS
        ) as client:
            results = await asyncio.gather(
                *[self._download(url, asset_dir, client, sem) for url in urls],
                return_exceptions=True,
            )

        url_map: dict[str, str] = {
            url: filename
            for url, filename in zip(urls, results, strict=False)
            if isinstance(filename, str)
        }
        return self._rewrite(html, url_map, slug)

    def _discover_images(self, html: str) -> list[str]:
        """Return deduplicated list of http(s) img src URLs found in *html*.

        Args:
            html: HTML to scan.

        Returns:
            Ordered, deduplicated list of absolute image URLs. ``data:`` URIs
            are excluded.
        """
        seen: set[str] = set()
        urls: list[str] = []
        for tag_m in _IMG_TAG_RE.finditer(html):
            src_m = _SRC_ATTR_RE.search(tag_m.group(0))
            if not src_m:
                continue
            src = src_m.group(1)
            if src.startswith("data:") or src in seen:
                continue
            seen.add(src)
            urls.append(src)
        return urls

    async def _download(
        self,
        url: str,
        asset_dir: Path,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
    ) -> str | None:
        """Download *url*, validate MIME type, and save to *asset_dir*.

        Args:
            url: Remote image URL.
            asset_dir: Destination directory for the downloaded file.
            client: Shared ``httpx.AsyncClient`` instance.
            sem: Semaphore controlling concurrency.

        Returns:
            Filename (e.g. ``'abc123def456.png'``) on success, ``None`` on any
            failure or if the server returns a non-image Content-Type.
        """
        async with sem:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except Exception:
                return None

            content_type = response.headers.get("content-type", "")
            mime = content_type.split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                return None

            ext = _ext_from_content_type(content_type) or _ext_from_url(url) or ".bin"
            data = response.content
            sha256 = hashlib.sha256(data).hexdigest()
            filename = f"{sha256[:16]}{ext}"
            (asset_dir / filename).write_bytes(data)
            return filename

    def _rewrite(self, html: str, url_map: dict[str, str], slug: str) -> str:
        """Replace img src values in *html* for URLs present in *url_map*.

        Also injects an ``alt`` attribute (original filename) when the tag has
        none, so ``markdownify`` in M4 produces correct Logseq-style embeds.

        Args:
            html: Original HTML.
            url_map: Mapping of remote URL to local filename.
            slug: Entry slug used to build the relative asset path.

        Returns:
            HTML with rewritten src attributes.
        """

        def _replace_tag(m: re.Match[str]) -> str:
            tag = m.group(0)
            src_m = _SRC_ATTR_RE.search(tag)
            if not src_m:
                return tag
            src = src_m.group(1)
            if src not in url_map:
                return tag
            local = f"../assets/{slug}/{url_map[src]}"
            new_tag = _SRC_ATTR_RE.sub(f'src="{local}"', tag)
            if not _ALT_ATTR_RE.search(new_tag):
                stripped = new_tag.rstrip()
                alt = f' alt="{_original_name(src)}"'
                if stripped.endswith("/>"):
                    new_tag = stripped[:-2].rstrip() + alt + " />"
                else:
                    new_tag = stripped[:-1].rstrip() + alt + ">"
            return new_tag

        return _IMG_TAG_RE.sub(_replace_tag, html)
