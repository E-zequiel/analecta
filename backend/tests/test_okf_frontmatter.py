"""T2: OKF frontmatter fields — ``type``, ``generated``, ``sources``.

New captures carry OKF-conformant provenance in their YAML frontmatter; the
existing vault is backfilled in place by an idempotent maintenance pass. The
body is never touched. Owner decisions D1/D10
(``~/Downloads/analecta-okf-cli-analisis.md``).
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from analecta.extraction.core import ExtractedContent
from analecta.maintenance import backfill_okf_frontmatter
from analecta.markdown.frontmatter import build_frontmatter


def _content(**meta: object) -> ExtractedContent:
    metadata: dict[str, object] = {
        "author": "Jane Doe",
        "published": "2026-02-23",
        **meta,
    }
    return ExtractedContent(
        title="Test Article",
        html="<p>x</p>",
        url="https://example.com/article",
        source_type="article",
        metadata=metadata,
    )


def _fm_dict(fm: str) -> dict[str, Any]:
    inner = fm.split("---\n", 2)[1]
    return yaml.safe_load(inner)


# ---------------------------------------------------------------------------
# build_frontmatter
# ---------------------------------------------------------------------------


def test_frontmatter_type_is_reference() -> None:
    data = _fm_dict(build_frontmatter(_content(), "2026-11-18T00:00:00+00:00"))
    assert data["type"] == "reference"


def test_frontmatter_generated_records_pipeline() -> None:
    created = "2026-11-18T12:00:00+00:00"
    data = _fm_dict(build_frontmatter(_content(), created))
    generated = data["generated"]
    assert generated["by"].startswith("analecta/")
    assert generated["at"] == created


def test_frontmatter_sources_record_resource_and_author() -> None:
    data = _fm_dict(build_frontmatter(_content(), "2026-11-18T00:00:00+00:00"))
    (source,) = data["sources"]
    assert source["resource"] == "https://example.com/article"
    assert source["author"] == "Jane Doe"


def test_frontmatter_sources_omit_last_modified_for_date_only_published() -> None:
    # OKF §5.1: timestamp-valued keys are ISO 8601 datetimes with offset; a
    # date-only `published` (article convention) must not masquerade as
    # `last_modified`.
    data = _fm_dict(build_frontmatter(_content(), "2026-11-18T00:00:00+00:00"))
    assert "last_modified" not in data["sources"][0]


def test_frontmatter_sources_last_modified_for_datetime_published() -> None:
    content = _content(published="2026-07-15T11:00:16.000Z")
    data = _fm_dict(build_frontmatter(content, "2026-11-18T00:00:00+00:00"))
    assert data["sources"][0]["last_modified"] == "2026-07-15T11:00:16.000Z"


def test_frontmatter_sources_without_author_metadata() -> None:
    content = _content(author=None)
    data = _fm_dict(build_frontmatter(content, "2026-11-18T00:00:00+00:00"))
    (source,) = data["sources"]
    assert "author" not in source


# ---------------------------------------------------------------------------
# backfill_okf_frontmatter
# ---------------------------------------------------------------------------


def _legacy_page(
    path: Path,
    *,
    published: str | None = None,
    with_type: bool = False,
) -> None:
    keys = [
        "title: Legacy",
        "url: https://example.com/legacy",
        "source_type: article",
        "created_at: '2026-04-30T10:00:00+00:00'",
        "tags: []",
        "read_state: unread",
    ]
    if published:
        keys.append(f"published: '{published}'")
    keys.append("author: Jane Doe")
    if with_type:
        keys.append("type: reference")
    path.write_text(
        "---\n" + "\n".join(keys) + "\n---\n\nBody text.\n", encoding="utf-8"
    )


def test_backfill_adds_okf_fields_to_legacy_page(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    page = pages / "2026-04-30-legacy.md"
    _legacy_page(page, published="2026-02-23")

    changed = backfill_okf_frontmatter(tmp_path)

    assert changed == 1
    inner = page.read_text(encoding="utf-8").split("---\n", 2)[1]
    data = yaml.safe_load(inner)
    assert data["type"] == "reference"
    assert data["generated"]["by"].startswith("analecta/")
    assert data["generated"]["at"] == "2026-04-30T10:00:00+00:00"
    (source,) = data["sources"]
    assert source["resource"] == "https://example.com/legacy"
    assert source["author"] == "Jane Doe"
    # Date-only published must not become last_modified.
    assert "last_modified" not in source
    assert "Body text." in page.read_text(encoding="utf-8")


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    page = pages / "2026-04-30-legacy.md"
    _legacy_page(page, published="2026-02-23")

    assert backfill_okf_frontmatter(tmp_path) == 1
    once = page.read_text(encoding="utf-8")
    assert backfill_okf_frontmatter(tmp_path) == 0
    assert page.read_text(encoding="utf-8") == once


def test_backfill_skips_pages_with_type(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    page = pages / "2026-04-30-okf.md"
    _legacy_page(page, with_type=True)
    before = page.read_text(encoding="utf-8")

    assert backfill_okf_frontmatter(tmp_path) == 0
    assert page.read_text(encoding="utf-8") == before


def test_backfill_skips_pages_without_frontmatter(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    plain = pages / "2026-04-30-plain.md"
    plain.write_text("No frontmatter.\n", encoding="utf-8")

    assert backfill_okf_frontmatter(tmp_path) == 0
    assert plain.read_text(encoding="utf-8") == "No frontmatter.\n"


def test_backfill_empty_vault_is_noop(tmp_path: Path) -> None:
    assert backfill_okf_frontmatter(tmp_path) == 0


@pytest.mark.parametrize("bad", ["", "---\nbroken yaml: [\n"])
def test_backfill_skips_unparseable_frontmatter(tmp_path: Path, bad: str) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    page = pages / "2026-04-30-broken.md"
    page.write_text(bad, encoding="utf-8")

    assert backfill_okf_frontmatter(tmp_path) == 0
