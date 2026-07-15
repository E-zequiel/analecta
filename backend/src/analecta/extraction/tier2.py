import os
from dataclasses import dataclass, field

import httpx2

from analecta.extraction.core import ExtractionError

_PORT = int(os.environ.get("ANALECTA_RENDER_PORT", "0"))
_TOKEN = os.environ.get("ANALECTA_RENDER_TOKEN", "")


@dataclass
class Tier2Result:
    """Result from the Electron render server (Defuddle or outerHtml fallback).

    Attributes:
        ok: True if Defuddle succeeded and ``content`` is populated.
        content: Cleaned HTML from Defuddle (browser mode, live DOM).
            Set when ``ok=True``.
        outer_html: Raw ``document.documentElement.outerHTML``.
            Set when ``ok=False``.
        title: Page title extracted by Defuddle.
        author: Author extracted by Defuddle.
        description: Meta description extracted by Defuddle.
        published: Publication date extracted by Defuddle.
        final_url: The rendered page's ``document.baseURI`` — the browser's
            own post-redirect (including client-side/JS redirects) URL.
            ``None`` if the render server predates this field or navigation
            failed before any document loaded.
        shots: Base64-encoded PNG bytes for interactive embeds (e.g. MDN
            live-code samples) captured via CDP before ``content``/
            ``outer_html`` were read, keyed by the id embedded in each
            placeholder ``<img src="https://analecta-shot.invalid/shot/{id}.png">``
            already spliced into both. Empty when no known embed selector
            matched anything on the page.
    """

    ok: bool
    content: str | None = None
    outer_html: str | None = None
    title: str | None = None
    author: str | None = None
    description: str | None = None
    published: str | None = None
    final_url: str | None = None
    shots: dict[str, str] = field(default_factory=dict)


async def render_url(url: str) -> Tier2Result:
    """Send *url* to the Electron render server and return the extraction result.

    Args:
        url: URL to scrape via Electron's Chromium.

    Returns:
        ``Tier2Result`` with Defuddle output or outer_html fallback.

    Raises:
        ExtractionError: If the render server port is not configured
            (sidecar not running inside Electron, or env vars not injected).
    """
    port = int(os.environ.get("ANALECTA_RENDER_PORT", _PORT))
    token = os.environ.get("ANALECTA_RENDER_TOKEN", _TOKEN)
    if not port:
        raise ExtractionError(
            "Electron render server not available (ANALECTA_RENDER_PORT not set)"
        )

    async with httpx2.AsyncClient(timeout=35.0) as client:
        resp = await client.post(
            f"http://127.0.0.1:{port}/render",
            json={"url": url},
            headers={"X-Render-Token": token},
        )
        resp.raise_for_status()
        data: dict[str, object] = resp.json()

    def _str(key: str) -> str | None:
        v = data.get(key)
        return str(v) if isinstance(v, str) else None

    shots_raw = data.get("shots")
    shots = (
        {
            k: v
            for k, v in shots_raw.items()
            if isinstance(k, str) and isinstance(v, str)
        }
        if isinstance(shots_raw, dict)
        else {}
    )

    return Tier2Result(
        ok=bool(data.get("ok", False)),
        content=_str("content"),
        outer_html=_str("outer_html"),
        title=_str("title"),
        author=_str("author"),
        description=_str("description"),
        published=_str("published"),
        final_url=_str("final_url"),
        shots=shots,
    )
