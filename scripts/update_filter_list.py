#!/usr/bin/env python3
"""Refreshes the vendored EasyPrivacy filter list used for Tier 2 tracker blocking.

Fetches the current list from easylist.to and overwrites
electron/filters/easyprivacy.txt. Run by hand, review the diff like a
dependency bump, and update before a release — this file is never fetched
at app runtime (see electron/main/tracker-blocking.ts).

Usage (from repo root):
    python scripts/update_filter_list.py
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TARGET_PATH = REPO_ROOT / "electron" / "filters" / "easyprivacy.txt"
SOURCE_URL = "https://easylist.to/easylist/easyprivacy.txt"
_FETCH_TIMEOUT = 30


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # pyright: ignore[reportAny]
        return resp.read()  # pyright: ignore[reportAny]


def main() -> int:
    """Fetches the current EasyPrivacy list and overwrites the vendored copy.

    Returns:
        Process exit code: 0 on success, 1 on fetch or validation failure.
    """
    print(f"Fetching {SOURCE_URL} ...")
    try:
        content = _fetch(SOURCE_URL)
    except (urllib.error.URLError, OSError) as exc:
        print(f"error: fetch failed: {exc}", file=sys.stderr)
        return 1

    text = content.decode("utf-8")
    if not text.startswith("[Adblock Plus"):
        print(
            "error: fetched content doesn't look like an Adblock Plus filter "
            "list (missing '[Adblock Plus' header) — refusing to overwrite "
            f"{TARGET_PATH}",
            file=sys.stderr,
        )
        return 1

    old_line_count = TARGET_PATH.read_text().count("\n") if TARGET_PATH.exists() else 0
    new_line_count = text.count("\n")

    tmp_path = TARGET_PATH.with_suffix(".txt.tmp")
    tmp_path.write_text(text)
    tmp_path.replace(TARGET_PATH)

    print(f"Wrote {TARGET_PATH} ({old_line_count} -> {new_line_count} lines).")
    print("Review the diff, then commit alongside a CHANGELOG entry if the ")
    print("list content meaningfully changed (not just a version/date bump).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
