"""Asset downloader — M3 pipeline."""

import asyncio
import functools
import hashlib
import importlib.resources
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx2
from bs4 import BeautifulSoup

from analecta.extraction.http_identity import build_headers
from analecta.extraction.ssrf import fetch_safely

_TIMEOUT = 30.0
_MAX_CONCURRENT = 5
_RETRY_DELAY_SECONDS = 1.0

_IMG_TAG_RE = re.compile(r"<img\s[^>]+>", re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
_ALT_ATTR_RE = re.compile(r"\balt=", re.IGNORECASE)

# Matches CommonMark inline image syntax whose URL is absolute or
# protocol-relative — the shape a saved Markdown entry can carry a live
# remote reference in. Does not match reference-style ![alt][ref] links
# (Analecta's own converter never emits them) and truncates at a literal
# ')' inside the URL (occurs in some Wikimedia filenames) — both are
# documented, currently-unhit limitations of the backfill path, not the
# go-forward extraction path.
_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(((?:https?:)?//[^\s)]+)(?:\s+"[^"]*")?\)')

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


def _resolve_nextjs_image(src: str) -> str:
    """Unwrap a Next.js ``/_next/image?url=…`` proxy URL to the underlying CDN URL.

    Args:
        src: Raw ``src`` attribute value from an ``<img>`` tag.

    Returns:
        Decoded CDN URL when the pattern matches, the original ``src`` otherwise.
    """
    if "/_next/image" not in src:
        return src
    try:
        qs = parse_qs(urlparse(src).query)
        urls = qs.get("url", [])
        return unquote(urls[0]) if urls else src
    except Exception:
        return src


@functools.lru_cache(maxsize=1)
def _placeholder_bytes() -> bytes:
    """Return the bundled 'image unavailable' placeholder SVG, cached.

    Returns:
        Raw SVG bytes, loaded once via ``importlib.resources`` (same
        packaging convention as the ``.sql`` migrations in
        ``storage/index.py``, so it survives PyInstaller's ``--onedir``
        bundling — see ``backend.spec``'s ``datas`` entry).
    """
    resource = (
        importlib.resources.files("analecta.extraction") / "static" / "broken-image.svg"
    )
    return resource.read_bytes()


def _placeholder_filename() -> str:
    """Return the deterministic filename the bundled placeholder is saved as.

    Content-addressed like any other asset — since the placeholder's bytes
    never change, its filename is stable, which lets callers recognize a
    placeholder result without a separate marker (see
    :meth:`AssetDownloader.localize_markdown`'s placeholder count).
    """
    return f"{hashlib.sha256(_placeholder_bytes()).hexdigest()[:16]}.svg"


async def _try_fetch(client: httpx2.AsyncClient, url: str) -> tuple[bytes, str] | None:
    """Attempt one GET of *url*, validating it's an image response.

    Args:
        client: Shared ``httpx2.AsyncClient`` instance.
        url: Remote image URL.

    Returns:
        ``(content_bytes, extension)`` on success, ``None`` on any network
        failure, non-2xx status, non-image ``Content-Type``, or a blocked
        scheme/host (see ``ssrf.py``) — *url* comes from already-fetched page
        content, so it's attacker-controlled; a blocked target degrades the
        same as any other failed fetch rather than raising.
    """
    try:
        response, _final_url = await fetch_safely(client, "GET", url)
        response.raise_for_status()
    except Exception:
        return None

    content_type = response.headers.get("content-type", "")
    mime = content_type.split(";")[0].strip().lower()
    if not mime.startswith("image/"):
        return None

    ext = _ext_from_content_type(content_type) or _ext_from_url(url) or ".bin"
    return response.content, ext


def _normalize_graphics(html: str) -> str:
    """Normalise image elements so ``_discover_images`` can find all real URLs.

    Three transformations are applied:

    1. **``<graphic>`` → ``<img>``**: trafilatura emits images as ``<graphic
       src="..." alt="..."/>`` (TEI-XML dialect).  Convert them to standard
       ``<img>`` before image discovery.

    2. **``data-src`` → ``src``**: Sites that use lazy loading (IntersectionObserver,
       ``loading="lazy"``) sometimes ship with an empty or base64 ``src`` and the
       real URL in ``data-src``.  Promote ``data-src`` to ``src`` so the
       downloader can fetch the real image.

    3. **``/_next/image?url=…`` → CDN URL**: Next.js image optimisation proxy
       URLs are relative to the origin and cannot be fetched by the sidecar.
       Unwrap them to the underlying absolute CDN URL before download.
    """
    soup = BeautifulSoup(html, "html.parser")

    for graphic in list(soup.find_all("graphic")):
        src = str(graphic.get("src", ""))
        alt = str(graphic.get("alt", ""))
        img = soup.new_tag("img", src=src, alt=alt)
        graphic.replace_with(img)

    for img in soup.find_all("img"):
        src = str(img.get("src", "") or "")
        # Promote data-src when src is absent or a data: placeholder.
        if (not src or src.startswith("data:")) and img.get("data-src"):
            src = str(img.get("data-src") or "")
            img["src"] = src
        # Resolve Next.js image proxy to the underlying CDN URL.
        if src and "/_next/image" in src:
            img["src"] = _resolve_nextjs_image(src)

    return str(soup)


class AssetDownloader:
    """Downloads images referenced in extracted HTML and rewrites src paths.

    Saved as ``{vault}/assets/{slug}/{sha256[:16]}.{ext}`` (content-addressed).
    A failed download (network error, non-2xx status, or a non-image
    response) is retried once; if the retry also fails, the reference is
    replaced with a bundled local placeholder image instead of the original
    remote URL. A preserved remote URL would re-fetch — and re-expose the
    reading IP — every time the entry is reopened, so no live remote
    ``src`` is ever written into a saved entry.
    """

    async def process(
        self,
        html: str,
        slug: str,
        vault_path: Path,
        base_url: str = "",
    ) -> str:
        """Download images in *html*, rewrite src attrs, return modified HTML.

        Args:
            html: Extracted HTML (M2 output).
            slug: Entry slug used as the asset subdirectory name.
            vault_path: Root vault directory.
            base_url: The source article's URL, used to resolve root-relative
                (``/foo.svg``) and protocol-relative (``//cdn.example.com/foo.svg``)
                ``src`` values to absolute URLs before download — the same
                resolution a browser applies. Pass ``""`` to skip resolution;
                non-absolute ``src`` values then fail to download and are left
                in the HTML unchanged.

        Returns:
            HTML with ``../assets/{slug}/...`` paths replacing remote src URLs
            for successfully downloaded images.
        """
        html = _normalize_graphics(html)
        urls = self._discover_images(html)
        if not urls:
            return html

        asset_dir = vault_path / "assets" / slug
        asset_dir.mkdir(parents=True, exist_ok=True)

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        async with httpx2.AsyncClient(
            timeout=_TIMEOUT,
            headers=build_headers("image"),
        ) as client:
            results = await asyncio.gather(
                *[
                    self._download(
                        urljoin(base_url, url) if base_url else url,
                        asset_dir,
                        client,
                        sem,
                    )
                    for url in urls
                ],
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
        client: httpx2.AsyncClient,
        sem: asyncio.Semaphore,
    ) -> str:
        """Download *url*, validate MIME type, and save to *asset_dir*.

        Args:
            url: Remote image URL.
            asset_dir: Destination directory for the downloaded file.
            client: Shared ``httpx2.AsyncClient`` instance.
            sem: Semaphore controlling concurrency.

        Returns:
            Filename of the downloaded image (e.g. ``'abc123def456.png'``).
            A network download that fails twice in a row falls back to the
            bundled placeholder's filename (see :meth:`_placeholder`) rather
            than ``None``.
        """
        async with sem:
            result = await _try_fetch(client, url)
            if result is None:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
                result = await _try_fetch(client, url)
            if result is None:
                return self._placeholder(asset_dir)

            data, ext = result
            sha256 = hashlib.sha256(data).hexdigest()
            filename = f"{sha256[:16]}{ext}"
            (asset_dir / filename).write_bytes(data)
            return filename

    def _placeholder(self, asset_dir: Path) -> str:
        """Write the bundled 'image unavailable' placeholder into *asset_dir*.

        Content-addressed like a real download, so it renders through the
        same ``analecta-file://`` path with no CSP/protocol changes. Reused
        across every failed image in an entry (and across entries) rather
        than duplicated, since the bytes — and therefore the hash — are
        always identical.

        Args:
            asset_dir: Destination directory (created by the caller).

        Returns:
            Filename of the placeholder (e.g. ``'abc123def456.svg'``).
        """
        filename = _placeholder_filename()
        path = asset_dir / filename
        if not path.exists():
            path.write_bytes(_placeholder_bytes())
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

    async def localize_markdown(
        self,
        markdown: str,
        slug: str,
        vault_path: Path,
        base_url: str = "",
    ) -> tuple[str, bool, int]:
        """Download remote images referenced in already-saved *markdown*.

        Backfill counterpart to :meth:`process`. That method rewrites HTML
        ``<img>`` tags *before* ``MarkdownConverter`` runs; by the time an
        entry is saved to disk, any surviving remote reference is already
        CommonMark ``![alt](url)`` syntax, which this method scans for and
        localizes instead — reusing :meth:`_download` (and its retry/
        placeholder fallback) as the shared core, rather than duplicating
        it.

        Args:
            markdown: Saved entry content to scan.
            slug: Entry slug — must match the ``assets/{slug}/`` directory
                the entry's other images already live in (or would have,
                had the original extraction fully succeeded).
            vault_path: Root vault directory.
            base_url: The entry's original source URL, used to resolve
                protocol-relative (``//cdn.example.com/x.png``) image URLs
                — same resolution :meth:`process` applies at extraction
                time. Pass ``""`` to skip resolution.

        Returns:
            ``(rewritten_markdown, changed, placeholder_count)``.
            *changed* is ``False`` when no remote image references were
            found (the expected common case). *placeholder_count* is how
            many of the rewritten references fell back to the local
            placeholder rather than a successful re-download, so a caller
            can distinguish "recovered" from "permanently unavailable"
            instead of a single opaque count.
        """
        urls = list(dict.fromkeys(m.group(2) for m in _MD_IMAGE_RE.finditer(markdown)))
        if not urls:
            return markdown, False, 0

        asset_dir = vault_path / "assets" / slug
        asset_dir.mkdir(parents=True, exist_ok=True)

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        async with httpx2.AsyncClient(
            timeout=_TIMEOUT,
            headers=build_headers("image"),
        ) as client:
            results = await asyncio.gather(
                *[
                    self._download(
                        urljoin(base_url, url) if base_url else url,
                        asset_dir,
                        client,
                        sem,
                    )
                    for url in urls
                ],
                return_exceptions=True,
            )

        url_map: dict[str, str] = {
            url: filename
            for url, filename in zip(urls, results, strict=False)
            if isinstance(filename, str)
        }
        if not url_map:
            return markdown, False, 0

        placeholder_name = _placeholder_filename()
        placeholder_count = sum(1 for f in url_map.values() if f == placeholder_name)

        return self._rewrite_markdown(markdown, url_map, slug), True, placeholder_count

    def _rewrite_markdown(
        self, markdown: str, url_map: dict[str, str], slug: str
    ) -> str:
        """Replace ``![alt](url)`` image references in *markdown* for URLs in *url_map*.

        Args:
            markdown: Original Markdown content.
            url_map: Mapping of remote URL to local filename.
            slug: Entry slug used to build the relative asset path.

        Returns:
            Markdown with rewritten image references.
        """

        def _replace(m: re.Match[str]) -> str:
            alt, url = m.group(1), m.group(2)
            if url not in url_map:
                return m.group(0)
            return f"![{alt}](../assets/{slug}/{url_map[url]})"

        return _MD_IMAGE_RE.sub(_replace, markdown)
