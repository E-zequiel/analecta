"""T4: re-extraction filename collision (S9) and resolution asymmetry (S10).

S9 — re-extracting a URL whose earlier capture was deleted (only
possible same-day: the ``YYYY-MM-DD-`` filename prefix comes from the
extraction date) lands on the same filename. When deprecated syntheses
cite that path (their ``sources[]``/``sources_basis`` resource), the
re-extraction must warn — on CLI stderr and through an additive signal
in the extract API response the frontend can show — while staying a
non-error condition: extraction succeeds (exit 0 / HTTP 200), the new
entry exists, and nothing is un-deprecated (the old synthesis stays
deprecated; revival stays deliberate per S6). A re-extraction with no
citing deprecated synthesis carries no warning.

S10 — known asymmetry, pinned as documented behavior: wikilinks resolve
by title and self-heal when a same-title entry re-appears; the
``sources[]``/``sources_basis`` citation resolves by path and keeps the
synthesis stale (S2b) until deliberate revival/update. Both states are
observable and stable, with no traceback.

Pinned shape decision (flagged for the owner): the API signal is an
additive ``warnings: list[str]`` field on the extract response, present
on every extract response (empty when no collision) so the frontend
reads one uniform shape; a per-response header or an SSE event are the
documented alternatives. The CLI pin is emitted anywhere inside the
``run(["extract", ...])`` invocation (stderr), so it holds whichever
layer renders it (pipeline, standalone writer, or CLI branch).

Demonstrated-red note: at HEAD (T1-T3 green) no collision warning
exists — the S9 pins fail on the missing stderr warning / missing
``warnings`` response field. The S10 pins are green at HEAD by design:
they pin pre-existing resolution mechanics (read-time title resolution
in get_backlinks; path-based staleness) now documented through the
re-extraction flow.

CLI note: the ``extract`` subcommand resolves its vault through
``load_config()`` (the ``vault_path`` override does not apply to
extract), so the tests inject the temp vault by patching
``analecta.cli.load_config``; attached mode is neutralized by patching
``analecta.cli._attached_post``, and extraction itself by patching
``analecta.cli.extract`` (no network). The standalone extract path runs
its own event loop, so the CLI invocation goes through
``asyncio.to_thread`` from the async tests.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
import pytest_asyncio
import yaml
from fastapi import FastAPI
from pytest_mock import MockerFixture

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.api.routes.extract import router as extract_router
from analecta.cli import run
from analecta.config import AppConfig
from analecta.extraction.assets import AssetDownloader
from analecta.extraction.core import ExtractedContent
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager

_COLLISION_TITLE = "Artículo colisión"
_ASYMMETRY_TITLE = "Artículo asimetría"
_SYNTH_TITLE = "Síntesis colisión"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_vault: Path) -> AsyncGenerator[httpx2.AsyncClient]:
    """Full-app async client wired in-process (httpx2 ASGI transport).

    Local mirror of the conftest ``client`` fixture (T1b), extended with
    the extract router: wires ``app.state`` directly because
    ``ASGITransport`` does not run the FastAPI lifespan and the
    singletons ``get_index``/``get_vault``/``get_event_bus`` read from
    ``app.state`` — the ``_make_app`` pattern of test_counts_synthesis.py
    and the local mirrors of test_synthesis.py /
    test_synthesis_deprecation.py.
    """
    config = AppConfig(vault_path=tmp_vault)
    app = FastAPI()
    app.include_router(entries_router, prefix="/api/v1")
    app.include_router(extract_router, prefix="/api/v1")
    index = VaultIndex(tmp_vault / "analecta.db")
    app.state.config = config
    app.state.index = index
    app.state.event_bus = EventBus()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    index.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content(title: str, url: str) -> ExtractedContent:
    """Fake extraction result (no network, deterministic title)."""
    return ExtractedContent(
        title=title,
        html="<p>Cuerpo del artículo.</p>",
        url=url,
        source_type="article",
        metadata={},
    )


def _patch_cli_extract(
    mocker: MockerFixture, tmp_vault: Path, title: str, url: str
) -> None:
    """Force standalone CLI extract against the temp vault, no network."""
    mocker.patch("analecta.cli._attached_post", return_value=None)
    mocker.patch(
        "analecta.cli.load_config", return_value=AppConfig(vault_path=tmp_vault)
    )

    async def fake_extract(target: str) -> ExtractedContent:
        return _content(title, url)

    mocker.patch("analecta.cli.extract", fake_extract)


def _synthesize(
    tmp_vault: Path, title: str, body: str, source_ids: list[int | None]
) -> None:
    """Create a synthesis through the CLI surface (canonical frontmatter)."""
    argv = ["synthesize", "--title", title, "--body", body, "--by", "bot/x"]
    for sid in source_ids:
        assert sid is not None
        argv += ["--source", str(sid)]
    assert run(argv, vault_path=tmp_vault) == 0


def _capture_by_title(index: VaultIndex, title: str) -> EntryRecord:
    """Return the sole reference capture with *title*."""
    matches = [
        r
        for r in index.list_entries()
        if r.source_type == "article" and r.title == title
    ]
    assert len(matches) == 1, f"expected one capture named {title!r}"
    return matches[0]


def _synthesis_by_title(index: VaultIndex, title: str) -> EntryRecord:
    matches = [
        r
        for r in index.list_entries()
        if r.source_type == "synthesis" and r.title == title
    ]
    assert len(matches) == 1, f"expected one synthesis named {title!r}"
    return matches[0]


def _flag(index: VaultIndex, entry_id: int | None) -> bool:
    """Read the derived ``deprecated`` flag (T1-pinned column) via SQL."""
    assert entry_id is not None
    row = index._conn.execute(
        "SELECT deprecated FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()
    assert row is not None
    return bool(row["deprecated"])


def _frontmatter(page: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter dict of a page."""
    text = page.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    data = yaml.safe_load(text[4 : text.index("\n---\n")])
    assert isinstance(data, dict)
    return data


def _resource_of(record: EntryRecord) -> str:
    """Vault-relative page resource, the ``sources[].resource`` shape."""
    return f"/pages/{Path(record.file_path).name}"


def _write_reappeared_entry(
    index: VaultIndex, tmp_vault: Path, title: str, url: str
) -> EntryRecord:
    """Write a re-appeared capture at today's path and index it.

    The re-extraction end state (same title, same URL, new page) per the
    established constructed-reappearance pattern of
    test_synthesis_update.py — deliberately dated today so the path
    differs from any past-dated original.
    """
    path = VaultManager(tmp_vault).page_path(title, datetime.now(tz=UTC))
    path.write_text(
        f"---\ntitle: '{title}'\n---\n\nCuerpo reextraído.\n", encoding="utf-8"
    )
    record = EntryRecord(
        title=title,
        url=url,
        file_path=str(path),
        source_type="article",
        created_at=datetime.now(tz=UTC).isoformat(),
        updated_at=datetime.now(tz=UTC).isoformat(),
    )
    record.id = index.add_entry(record)
    return record


# ---------------------------------------------------------------------------
# S9: filename-collision warning — CLI stderr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collision_reextraction_warns_on_cli_stderr(
    tmp_vault: Path,
    index: VaultIndex,
    client: httpx2.AsyncClient,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S9: re-extracting a deleted capture's URL on the same day warns on
    stderr — naming the collision path and the deprecated synthesis
    citing it — while staying a non-error condition (exit 0, success
    output unchanged) and never un-deprecating the old synthesis."""
    url = "https://example.com/colision"
    _patch_cli_extract(mocker, tmp_vault, _COLLISION_TITLE, url)

    code = await asyncio.to_thread(run, ["extract", url])
    assert code == 0
    first = capsys.readouterr()
    # Control: the fresh first extraction prints no warning (nothing
    # deprecated cites anything yet).
    assert first.err == ""

    capture = _capture_by_title(index, _COLLISION_TITLE)
    assert capture.id is not None
    _synthesize(tmp_vault, _SYNTH_TITLE, "Cuerpo de la síntesis.", [capture.id])
    synth = _synthesis_by_title(index, _SYNTH_TITLE)
    assert synth.id is not None

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204
    assert _flag(index, synth.id) is True

    code = await asyncio.to_thread(run, ["extract", url])
    captured = capsys.readouterr()

    # Not an error condition: exit 0 and the success output unchanged.
    assert code == 0
    assert "saved:" in captured.out
    # The warning goes to stderr, naming the collision path and the
    # deprecated synthesis that cites it.
    assert Path(capture.file_path).name in captured.err
    assert _SYNTH_TITLE in captured.err
    # Nothing un-deprecated: the old synthesis stays deprecated.
    assert _flag(index, synth.id) is True
    assert _frontmatter(Path(synth.file_path))["status"] == "deprecated"


# ---------------------------------------------------------------------------
# S9: filename-collision warning — API/UI additive signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collision_reextraction_warns_in_extract_response(
    tmp_vault: Path,
    index: VaultIndex,
    client: httpx2.AsyncClient,
    mocker: MockerFixture,
) -> None:
    """S9: the extract response carries an additive warning signal the
    frontend can show — naming the collision path and the deprecated
    synthesis citing it — while extraction succeeds (200), the new entry
    exists, and nothing is un-deprecated. Shape decision pinned here:
    ``warnings: list[str]`` on the extract response, always present
    (empty when no collision) — see module docstring."""
    url = "https://example.com/colision"
    mocker.patch(
        "analecta.extraction.pipeline.extract",
        return_value=_content(_COLLISION_TITLE, url),
    )
    mocker.patch.object(
        AssetDownloader, "process", return_value="<p>Cuerpo del artículo.</p>"
    )

    r1 = await client.post("/api/v1/extract", json={"url": url})
    assert r1.status_code == 200
    old = r1.json()

    _synthesize(tmp_vault, _SYNTH_TITLE, "Cuerpo de la síntesis.", [old["id"]])
    synth = _synthesis_by_title(index, _SYNTH_TITLE)
    assert synth.id is not None

    response = await client.delete(f"/api/v1/entries/{old['id']}")
    assert response.status_code == 204
    assert _flag(index, synth.id) is True

    r2 = await client.post("/api/v1/extract", json={"url": url})

    # Not an error condition: extraction succeeds and lands on the same
    # filename (the same-day collision this spec targets).
    assert r2.status_code == 200
    data = r2.json()
    assert data["url"] == url
    assert data["file_path"] == old["file_path"]
    assert data["id"] != old["id"]
    # The additive warning names the collision path and the citing
    # deprecated synthesis.
    assert "warnings" in data, data
    assert any(
        Path(old["file_path"]).name in w and _SYNTH_TITLE in w for w in data["warnings"]
    )
    # The new entry exists and nothing is un-deprecated.
    listing = await client.get("/api/v1/entries")
    assert listing.status_code == 200
    assert len(listing.json()) == 2
    assert _flag(index, data["id"]) is False
    assert _flag(index, synth.id) is True
    assert _frontmatter(Path(synth.file_path))["status"] == "deprecated"


@pytest.mark.asyncio
async def test_reextraction_without_citing_synthesis_carries_no_warning(
    tmp_vault: Path,
    index: VaultIndex,
    client: httpx2.AsyncClient,
    mocker: MockerFixture,
) -> None:
    """S9 control: a same-day re-extraction of an entry that had no
    citing syntheses carries no warning — the additive signal is present
    (uniform shape) but empty, and extraction succeeds."""
    url = "https://example.com/sin-cites"
    mocker.patch(
        "analecta.extraction.pipeline.extract",
        return_value=_content("Artículo sin cites", url),
    )
    mocker.patch.object(
        AssetDownloader, "process", return_value="<p>Cuerpo del artículo.</p>"
    )

    r1 = await client.post("/api/v1/extract", json={"url": url})
    assert r1.status_code == 200
    old = r1.json()

    response = await client.delete(f"/api/v1/entries/{old['id']}")
    assert response.status_code == 204

    r2 = await client.post("/api/v1/extract", json={"url": url})

    assert r2.status_code == 200
    data = r2.json()
    assert data["url"] == url
    assert data["file_path"] == old["file_path"]
    assert "warnings" in data, data
    assert data["warnings"] == []


# ---------------------------------------------------------------------------
# S10: title-vs-path resolution asymmetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wikilink_self_heals_after_same_day_reextraction(
    tmp_vault: Path,
    index: VaultIndex,
    client: httpx2.AsyncClient,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S10 (title side): a deprecated synthesis's wikilink resolves by
    title and reconnects to the re-extracted entry — get_backlinks of the
    new entry reports the (still deprecated) synthesis again. Green at
    HEAD by design: resolution is read-time, by title. The stale verdict
    is deliberately not pinned here — same-day same-path re-extraction
    makes it content-dependent (the basis hash may match), and the path
    side is pinned by the asymmetry test below."""
    url = "https://example.com/colision"
    _patch_cli_extract(mocker, tmp_vault, _COLLISION_TITLE, url)

    code = await asyncio.to_thread(run, ["extract", url])
    assert code == 0
    capsys.readouterr()
    capture = _capture_by_title(index, _COLLISION_TITLE)
    assert capture.id is not None
    _synthesize(
        tmp_vault,
        _SYNTH_TITLE,
        "Lectura de [[Artículo colisión]] para el análisis.",
        [capture.id],
    )
    synth = _synthesis_by_title(index, _SYNTH_TITLE)
    assert synth.id is not None

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204
    assert _flag(index, synth.id) is True

    code = await asyncio.to_thread(run, ["extract", url])
    assert code == 0
    capsys.readouterr()

    reextracted = _capture_by_title(index, _COLLISION_TITLE)
    assert reextracted.id is not None
    assert reextracted.id != capture.id
    backlinks = index.get_backlinks(reextracted.id)
    assert any(b.source_id == synth.id for b in backlinks)
    # The healed network did not un-deprecate anything.
    assert _flag(index, synth.id) is True
    assert _frontmatter(Path(synth.file_path))["status"] == "deprecated"


@pytest.mark.asyncio
async def test_same_title_reappearance_heals_wikilink_but_sources_stay_stale(
    tmp_vault: Path,
    index: VaultIndex,
    client: httpx2.AsyncClient,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S10 (asymmetry): when a same-title entry re-appears at a different
    path (the not-same-day case), the wikilink network self-heals — the
    deprecated synthesis resolves to the new entry again — while the
    ``sources[]``/``sources_basis`` citation resolves by path and stays
    stale: the synthesis remains deprecated AND stale by S2b, with no
    traceback on the reporting surfaces. Green at HEAD by design: both
    resolution mechanics pre-exist; this pins the asymmetry as
    documented behavior."""
    mocker.patch("analecta.cli._attached_get", return_value=None)
    VaultManager(tmp_vault).ensure_dirs()
    past = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    original_path = VaultManager(tmp_vault).page_path(_ASYMMETRY_TITLE, past)
    original_path.write_text(
        f"---\ntitle: '{_ASYMMETRY_TITLE}'\n---\n\nCuerpo original.\n",
        encoding="utf-8",
    )
    capture = EntryRecord(
        title=_ASYMMETRY_TITLE,
        url="https://example.com/asimetria",
        file_path=str(original_path),
        source_type="article",
        created_at=past.isoformat(),
        updated_at=past.isoformat(),
    )
    capture.id = index.add_entry(capture)
    assert capture.id is not None

    _synthesize(
        tmp_vault,
        _SYNTH_TITLE,
        "Lectura de [[Artículo asimetría]] para el análisis.",
        [capture.id],
    )
    synth = _synthesis_by_title(index, _SYNTH_TITLE)
    assert synth.id is not None

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204
    assert _flag(index, synth.id) is True
    # Drain the setup's own output so only the invocation under test is
    # observed (the setup legitimately prints its creation lines).
    capsys.readouterr()

    # The capture re-appears: same title, same URL, today's path.
    reappeared = _write_reappeared_entry(
        index, tmp_vault, _ASYMMETRY_TITLE, capture.url
    )
    assert reappeared.id is not None
    assert Path(reappeared.file_path) != original_path

    # Title side: the wikilink network reconnected to the new entry.
    backlinks = index.get_backlinks(reappeared.id)
    assert any(b.source_id == synth.id for b in backlinks)

    # Path side: the deprecated synthesis remains stale (its
    # sources[]/sources_basis resource still names the old path) and
    # deprecated — stable, no traceback, on the reporting surfaces.
    code = run(["index", "--json"], vault_path=tmp_vault)
    captured = capsys.readouterr()
    assert code == 0
    assert "Traceback" not in captured.out + captured.err
    data = json.loads(captured.out)
    synths = {item["path"]: item for item in data if item["type"] == "synthesis"}
    item = synths[_resource_of(synth)]
    assert item["deprecated"] is True
    assert item["stale"] is True
    # Nothing un-deprecated: the page mark and the flag are untouched.
    assert _frontmatter(Path(synth.file_path))["status"] == "deprecated"
    assert _flag(index, synth.id) is True
