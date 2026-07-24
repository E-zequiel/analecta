"""Coherent, generic browser headers for Tier 1 fetches.

Analecta identifies as a current Chrome on Linux — never a custom string
tied to the project or its maintainer — so a Tier 1 fetch doesn't stick out
against normal web traffic. See docs/privacy.md for the full threat model
and what this does and doesn't protect.

The Chrome major version is single-sourced from Electron's own bundled
Chromium (``process.versions.chrome``, passed down as ``ANALECTA_CHROME_MAJOR``
at sidecar spawn — see electron/main/sidecar.ts and chrome-identity.ts), so
the claimed UA borrows the real version actually running the app rather than
a hardcoded one that would age into an anomaly — this module never goes
stale on its own. ``_FALLBACK_CHROME_MAJOR`` only backs standalone runs with
no Electron parent (``/dev``, pytest) — bump it occasionally so a fresh
checkout doesn't claim an ancient Chrome.
"""

from __future__ import annotations

import os
from typing import Literal

_FALLBACK_CHROME_MAJOR = "150"

Purpose = Literal["document", "image", "api"]


def _chrome_major() -> str:
    return os.environ.get("ANALECTA_CHROME_MAJOR") or _FALLBACK_CHROME_MAJOR


def _user_agent() -> str:
    # Mirrors Chrome's own reduced/frozen UA format (minor/build/patch
    # zeroed) rather than a real, specific version — see
    # https://www.chromium.org/updates/ua-reduction/. A precise version
    # would itself be a fingerprint and would age into an anomaly.
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{_chrome_major()}.0.0.0 Safari/537.36"
    )


def _sec_ch_ua() -> str:
    major = _chrome_major()
    # Chrome GREASEs the "not a brand" entry per-release to stop hardcoded
    # parsing; this is a plausible fixed placeholder, not a wire-exact
    # replica — good enough to avoid the "UA says Chrome, headers say
    # Python" tell, not a defense against a determined fingerprinter.
    return f'"Chromium";v="{major}", "Not)A;Brand";v="8", "Google Chrome";v="{major}"'


_COMMON_HEADERS = {
    # Generic, not the system locale — a real es-AR Accept-Language is
    # itself a small deanonymizer.
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Linux"',
}


def build_headers(purpose: Purpose) -> dict[str, str]:
    """Coherent Chrome-shaped headers for a Tier 1 request.

    Args:
        purpose: ``"document"`` for a top-level page fetch, ``"image"`` for
            an embedded asset fetch, or ``"api"`` for an XHR/fetch-style
            call (e.g. an oEmbed lookup).

    Returns:
        Header dict to merge into the request. Deliberately omits
        ``Accept-Encoding`` — httpx2 already negotiates the correct value
        for the codecs actually installed; overriding it risks claiming
        brotli/zstd support that isn't there and getting back undecodable
        bytes.
    """
    headers = {
        "User-Agent": _user_agent(),
        "Sec-CH-UA": _sec_ch_ua(),
        **_COMMON_HEADERS,
    }
    if purpose == "document":
        headers.update(
            {
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8"
                ),
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        )
    elif purpose == "image":
        headers.update(
            {
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,*/*;q=0.8",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",
            }
        )
    else:
        headers.update(
            {
                "Accept": "*/*",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
            }
        )
    return headers
