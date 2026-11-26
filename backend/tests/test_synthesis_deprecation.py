"""T1: capture deletion deprecates citing syntheses (S1/S2/S15/S17).

Owner decision C (D13 → OKF deprecation): deleting a capture entry no
longer deletes the synthesis entries citing it. Each citing synthesis
gains ``status: deprecated`` in its page frontmatter — every other
frontmatter key preserved, body untouched, atomic temp+``os.replace``
rewrite — and its DB row is preserved. Deleting a synthesis still never
touches its capture (unidirectional, unchanged).

Pinned design decisions (Agent A, for Agent B to implement):

- Derived DB flag (S2): an INTEGER 0/1 column ``deprecated`` on
  ``entries``, visible to SQL consumers directly. Its truth is the page
  frontmatter ``status: deprecated``; it is set write-through at
  deprecation time and re-derived from the page by the reconcile pass
  (mirrors the backlink_refs/FTS derived-state pattern: mtime-drift
  gated). S2 does not name the flag — this naming is the pinned
  contract (ambiguity A1).
- The flag derivation is frontmatter-driven for every entry whose page
  carries ``status: deprecated``, not gated on
  ``source_type == 'synthesis'`` — pinned reading of S2's "truth lives
  in the page frontmatter" (ambiguity A2).
- Crash-window (S17): when the deprecation rewrite fails mid-delete, the
  deletion is aborted (500): the capture row, page, and assets survive
  and the synthesis stays unmarked (owner decision A3, spec Log L8a).
  The hard-crash state (unmarked page, capture gone, clean mtime) is the
  documented limitation reconcile cannot infer from.
- Deletion surface: every behavioral pin goes through the public
  ``DELETE /api/v1/entries/{id}`` route, page files on disk, frontmatter
  content, and the reconcile pass. No test imports the storage deletion
  helper, so the S16 rename (``delete_syntheses_for_entry`` →
  ``deprecate_syntheses_for_entry``) cannot require test edits.

Demonstrated-red note: at HEAD the D13 cascade deletes citing syntheses
(rows and pages), and the derived flag does not exist. The S1 pins fail
on the cascade assertions; the S2/S17 flag pins fail with
``sqlite3.OperationalError: no such column: deprecated`` — the missing
mechanism IS the observed red, the same class the sibling
test_synthesis_freshness.py documents for its import-error red. The S15
pins are green at HEAD by design: they pin unchanged behavior.
"""

import os
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
from analecta.cli import run
from analecta.config import AppConfig
from analecta.manifest import MANIFEST_FILE_NAME, write_vault_manifest
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager


def _exists(path: Path) -> bool:
    """Sync seam for ASYNC240: blocking fs probe callable from async tests."""
    return path.exists()


def _read_bytes(path: Path) -> bytes:
    """Sync seam for ASYNC240: blocking read callable from async tests."""
    return path.read_bytes()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_vault: Path) -> AsyncGenerator[httpx2.AsyncClient]:
    """Full-app async client wired in-process (httpx2 ASGI transport).

    Local mirror of the conftest ``client`` fixture (T1b): wires
    ``app.state`` directly. ``ASGITransport`` does not run the FastAPI
    lifespan, so the singletons ``get_index``/``get_event_bus`` read from
    ``app.state`` are wired here, the ``_make_app`` pattern of
    test_counts_synthesis.py.
    """
    config = AppConfig(vault_path=tmp_vault)
    app = FastAPI()
    app.include_router(entries_router, prefix="/api/v1")
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


def _capture(index: VaultIndex, tmp_vault: Path, title: str, body: str) -> EntryRecord:
    """Write a real capture page on disk and index it."""
    manager = VaultManager(tmp_vault)
    manager.ensure_dirs()
    path = manager.page_path(title, datetime.now(tz=UTC))
    path.write_text(f"---\ntitle: '{title}'\n---\n\n{body}\n", encoding="utf-8")
    entry = EntryRecord(
        title=title,
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        file_path=str(path),
        source_type="article",
        created_at=_now(),
        updated_at=_now(),
    )
    entry.id = index.add_entry(entry)
    return entry


def _synthesize_via_cli(
    tmp_vault: Path, title: str, body: str, source_ids: list[int | None]
) -> None:
    """Create a synthesis through the CLI surface (canonical frontmatter)."""
    argv = ["synthesize", "--title", title, "--body", body, "--by", "bot/x"]
    for sid in source_ids:
        assert sid is not None
        argv += ["--source", str(sid)]
    assert run(argv, vault_path=tmp_vault) == 0


def _synthesis_by_title(index: VaultIndex, title: str) -> EntryRecord:
    matches = [
        r
        for r in index.list_entries()
        if r.source_type == "synthesis" and r.title == title
    ]
    assert len(matches) == 1, f"expected one synthesis named {title!r}"
    return matches[0]


def _flag(index: VaultIndex, entry_id: int | None) -> bool:
    """Read the derived ``deprecated`` flag (S2 pinned column) via SQL."""
    assert entry_id is not None
    row = index._conn.execute(
        "SELECT deprecated FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()
    assert row is not None
    return bool(row["deprecated"])


def _set_flag(index: VaultIndex, entry_id: int, value: int) -> None:
    """Hand-set the derived flag (simulates a crash-window write)."""
    index._conn.execute(
        "UPDATE entries SET deprecated = ? WHERE id = ?", (value, entry_id)
    )
    index._conn.commit()


def _frontmatter(page: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter dict of a page."""
    text = page.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    data = yaml.safe_load(text[4 : text.index("\n---\n")])
    assert isinstance(data, dict)
    return data


def _rewrite_frontmatter(page: Path, data: dict[str, Any]) -> None:
    """Re-serialize a page's frontmatter in place (out-of-app edit)."""
    text = page.read_text(encoding="utf-8")
    block = yaml.dump(data, allow_unicode=True, sort_keys=False)
    end = text.index("\n---\n") + len("\n---\n")
    page.write_text(f"---\n{block}---\n{text[end:]}", encoding="utf-8")


def _edit_frontmatter(page: Path, updates: dict[str, Any]) -> None:
    data = _frontmatter(page)
    data.update(updates)
    _rewrite_frontmatter(page, data)


def _remove_frontmatter_key(page: Path, key: str) -> None:
    data = _frontmatter(page)
    data.pop(key, None)
    _rewrite_frontmatter(page, data)


def _post_frontmatter_body_bytes(page: Path) -> bytes:
    """Post-frontmatter body bytes exactly as stored on disk."""
    data = page.read_bytes()
    assert data.startswith(b"---\n")
    end = data.index(b"\n---\n") + len(b"\n---\n")
    return data[end:]


def _bump_mtime(path: Path) -> None:
    """Advance a file's mtime, simulating an external edit."""
    stat = path.stat()
    os.utime(path, (stat.st_atime + 5.0, stat.st_mtime + 5.0))


# ---------------------------------------------------------------------------
# S1: deleting a capture deprecates the citing synthesis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_capture_deprecates_citing_synthesis_page_and_db_row(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
) -> None:
    """S1: deleting a capture no longer deletes the citing synthesis —
    the page survives with ``status: deprecated`` added to its
    frontmatter, every other frontmatter key preserved, the body
    untouched, and the DB row intact (same id, url, path, title)."""
    capture = _capture(index, tmp_vault, "Captura citada", "Cuerpo íntegro.")
    assert capture.id is not None
    _synthesize_via_cli(
        tmp_vault, "Síntesis citada", "Cuerpo de la síntesis.", [capture.id]
    )
    synth = _synthesis_by_title(index, "Síntesis citada")
    assert synth.id is not None
    page = Path(synth.file_path)
    pre_fm = _frontmatter(page)
    pre_body = _post_frontmatter_body_bytes(page)

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204

    # DB row preserved (S1).
    synth_after = index.get_entry(synth.id)
    assert synth_after is not None
    assert synth_after.id == synth.id
    assert synth_after.url == synth.url
    assert synth_after.file_path == synth.file_path
    assert synth_after.source_type == "synthesis"
    assert synth_after.title == synth.title
    assert synth_after.created_at == synth.created_at
    # Page survives: status: deprecated added, everything else preserved.
    assert _exists(page)
    assert _frontmatter(page) == {**pre_fm, "status": "deprecated"}
    assert _post_frontmatter_body_bytes(page) == pre_body
    # The API still exposes the surviving synthesis entry.
    lookup = await client.get(f"/api/v1/entries/{synth.id}")
    assert lookup.status_code == 200
    # The capture itself is gone.
    assert index.get_entry(capture.id) is None
    assert not _exists(Path(capture.file_path))


@pytest.mark.asyncio
async def test_deprecation_overwrites_preexisting_status_and_preserves_extra_keys(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
) -> None:
    """S1: a synthesis whose frontmatter already carries a ``status`` key
    (and extra hand-added keys) gets ``status`` overwritten to
    ``deprecated`` while every other key survives the rewrite."""
    capture = _capture(index, tmp_vault, "Captura extras", "Cuerpo íntegro.")
    _synthesize_via_cli(
        tmp_vault, "Síntesis extras", "Cuerpo de la síntesis.", [capture.id]
    )
    synth = _synthesis_by_title(index, "Síntesis extras")
    assert synth.id is not None
    page = Path(synth.file_path)
    _edit_frontmatter(page, {"status": "stable", "custom_key": "x"})
    pre_fm = _frontmatter(page)
    pre_body = _post_frontmatter_body_bytes(page)
    assert pre_fm["status"] == "stable"

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204

    assert index.get_entry(synth.id) is not None
    assert _exists(page)
    assert _frontmatter(page) == {**pre_fm, "status": "deprecated"}
    assert _post_frontmatter_body_bytes(page) == pre_body


@pytest.mark.asyncio
async def test_deprecation_rewrite_leaves_no_temp_files_and_publishes_complete_page(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
) -> None:
    """S1 (atomic rewrite, S19 pattern of okf-cli): after the deprecation
    the pages directory holds no leftover temp files of any name, and the
    published bytes are a single complete rewrite — parseable frontmatter
    plus the original body — not a truncated or interleaved file."""
    capture = _capture(index, tmp_vault, "Captura atómica", "Cuerpo íntegro.")
    _synthesize_via_cli(
        tmp_vault, "Síntesis atómica", "Cuerpo de la síntesis.", [capture.id]
    )
    synth = _synthesis_by_title(index, "Síntesis atómica")
    assert synth.id is not None
    page = Path(synth.file_path)
    pre_fm = _frontmatter(page)
    pre_body = _post_frontmatter_body_bytes(page)

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204

    pages_dir = tmp_vault / "pages"
    assert {p.name for p in pages_dir.iterdir()} == {page.name}
    assert index.get_entry(synth.id) is not None
    assert _frontmatter(page) == {**pre_fm, "status": "deprecated"}
    assert _post_frontmatter_body_bytes(page) == pre_body


# ---------------------------------------------------------------------------
# S2: derived DB flag, re-derived from the page by reconcile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deprecation_sets_derived_db_flag_immediately(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
) -> None:
    """S2: the derived ``deprecated`` flag is visible to SQL consumers
    immediately after the deprecation — without an explicit reconcile —
    for every synthesis citing the deleted capture, and stays 0 for
    syntheses citing other captures."""
    a = _capture(index, tmp_vault, "Captura bandera A", "Cuerpo A.")
    b = _capture(index, tmp_vault, "Captura bandera B", "Cuerpo B, con ñ.")
    _synthesize_via_cli(tmp_vault, "Síntesis bandera A", "Cuerpo de A.", [a.id])
    _synthesize_via_cli(tmp_vault, "Síntesis bandera B", "Cuerpo de B.", [b.id])
    sa = _synthesis_by_title(index, "Síntesis bandera A")
    sb = _synthesis_by_title(index, "Síntesis bandera B")

    response = await client.delete(f"/api/v1/entries/{a.id}")
    assert response.status_code == 204

    assert _flag(index, sa.id) is True
    assert _flag(index, sb.id) is False


def test_reconcile_rederives_deprecated_flag_from_page_frontmatter(
    index: VaultIndex,
    tmp_vault: Path,
) -> None:
    """S2: the flag is re-derived from the page by reconcile — a synthesis
    page hand-marked ``status: deprecated`` outside the app flips the
    flag to 1 on the next reconcile pass (mtime-drift gated, like the
    backlink_refs/FTS derived state)."""
    capture = _capture(index, tmp_vault, "Captura derivada", "Cuerpo íntegro.")
    _synthesize_via_cli(
        tmp_vault, "Síntesis derivada", "Cuerpo de la síntesis.", [capture.id]
    )
    synth = _synthesis_by_title(index, "Síntesis derivada")
    assert _flag(index, synth.id) is False

    page = Path(synth.file_path)
    _edit_frontmatter(page, {"status": "deprecated"})
    _bump_mtime(page)

    index.reconcile_stale_entries()
    assert _flag(index, synth.id) is True


@pytest.mark.asyncio
async def test_reconcile_clears_flag_when_page_key_removed_out_of_app(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
) -> None:
    """S2: frontmatter truth is authoritative — when the
    ``status: deprecated`` key is removed from the page outside the app,
    the next reconcile pass clears the derived flag back to 0."""
    capture = _capture(index, tmp_vault, "Captura limpieza", "Cuerpo íntegro.")
    _synthesize_via_cli(
        tmp_vault, "Síntesis limpieza", "Cuerpo de la síntesis.", [capture.id]
    )

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204
    synth = _synthesis_by_title(index, "Síntesis limpieza")
    assert _flag(index, synth.id) is True

    page = Path(synth.file_path)
    _remove_frontmatter_key(page, "status")
    assert "status" not in _frontmatter(page)
    _bump_mtime(page)

    index.reconcile_stale_entries()
    assert _flag(index, synth.id) is False


def test_reconcile_derives_flag_for_any_page_marked_deprecated(
    index: VaultIndex,
    tmp_vault: Path,
) -> None:
    """S2 pinned reading (ambiguity A2): the derivation is
    frontmatter-driven for every entry, not gated on source_type — a
    reference capture whose page is hand-marked ``status: deprecated``
    flips the flag too."""
    capture = _capture(index, tmp_vault, "Captura marcada", "Cuerpo íntegro.")
    assert _flag(index, capture.id) is False

    page = Path(capture.file_path)
    _edit_frontmatter(page, {"status": "deprecated"})
    _bump_mtime(page)

    index.reconcile_stale_entries()
    assert _flag(index, capture.id) is True


# ---------------------------------------------------------------------------
# S17: crash window — rewrite fails or is interrupted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_deprecation_rewrite_aborts_capture_deletion(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
    mocker: MockerFixture,
) -> None:
    """S17 (owner decision A3, 2026-11-26): when the deprecation rewrite
    fails mid-delete (the pinned atomic-publish step ``os.replace``
    raises), the deletion is aborted: the API returns an error (500),
    the capture row, page, and assets survive untouched, and the
    synthesis stays unmarked — frontmatter truth is authoritative. The
    partial state (any syntheses already rewritten stay marked, the
    capture survives) is retryable.

    Red at HEAD via the D13 cascade (the synthesis row and page are
    deleted). The failure is injected at ``os.replace`` — the
    atomic-publish step S1 pins — rather than at any
    implementation-private name; the implementation must bind it via the
    ``os`` module attribute so the injection seam holds.
    """
    capture = _capture(index, tmp_vault, "Captura crash", "Cuerpo íntegro.")
    assert capture.id is not None
    _synthesize_via_cli(
        tmp_vault, "Síntesis crash", "Cuerpo de la síntesis.", [capture.id]
    )
    synth = _synthesis_by_title(index, "Síntesis crash")
    assert synth.id is not None
    page = Path(synth.file_path)
    pre_bytes = _read_bytes(page)
    assert "status" not in _frontmatter(page)

    mocker.patch("os.replace", side_effect=RuntimeError("simulated crash"))

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 500

    # The deletion aborted: capture row, page, and assets survive.
    assert index.get_entry(capture.id) is not None
    assert _exists(Path(capture.file_path))
    # The synthesis stayed unmarked: page byte-identical, row preserved,
    # derived flag not set.
    assert index.get_entry(synth.id) is not None
    assert _exists(page)
    assert _read_bytes(page) == pre_bytes
    assert _flag(index, synth.id) is False


def test_reconcile_cannot_infer_deprecation_after_crash_window(
    index: VaultIndex,
    tmp_vault: Path,
) -> None:
    """S17: reconcile derives from frontmatter only and never infers
    deprecation — a synthesis left unmarked by a crash window (its cited
    capture is gone, its page unmarked) stays unmarked with flag 0 after
    reconcile, even when the page is revisited (mtime bumped)."""
    capture = _capture(index, tmp_vault, "Captura ventana", "Cuerpo íntegro.")
    _synthesize_via_cli(
        tmp_vault, "Síntesis ventana", "Cuerpo de la síntesis.", [capture.id]
    )
    synth = _synthesis_by_title(index, "Síntesis ventana")
    page = Path(synth.file_path)

    # Hand-construct the crash-window end state: capture row and page
    # gone, synthesis untouched and unmarked.
    index._conn.execute("DELETE FROM entries WHERE id = ?", (capture.id,))
    index._conn.commit()
    Path(capture.file_path).unlink()
    assert "status" not in _frontmatter(page)

    _bump_mtime(page)
    index.reconcile_stale_entries()

    assert _flag(index, synth.id) is False
    assert "status" not in _frontmatter(page)


def test_reconcile_clears_flag_set_without_page_mark_crash_window(
    index: VaultIndex,
    tmp_vault: Path,
) -> None:
    """S17 corollary: a flag set without the page mark (the app crashed
    between the flag write and the page rewrite) is cleared by reconcile
    once the page is revisited — frontmatter truth is authoritative. The
    mtime is bumped to force the re-derivation visit: the derived-state
    pattern is mtime-gated, so the no-drift window stays a documented S17
    limitation, not a pinned behavior."""
    capture = _capture(index, tmp_vault, "Captura flag fantasma", "Cuerpo íntegro.")
    _synthesize_via_cli(
        tmp_vault, "Síntesis flag fantasma", "Cuerpo de la síntesis.", [capture.id]
    )
    synth = _synthesis_by_title(index, "Síntesis flag fantasma")
    assert synth.id is not None
    page = Path(synth.file_path)
    assert "status" not in _frontmatter(page)

    _set_flag(index, synth.id, 1)
    _bump_mtime(page)

    index.reconcile_stale_entries()
    assert _flag(index, synth.id) is False
    assert "status" not in _frontmatter(page)


def test_manifest_documents_deprecation_crash_window(tmp_path: Path) -> None:
    """S17 (documentation pin): the vault manifest documents the crash
    window — an interrupted deprecation rewrite leaves the synthesis
    unmarked, and reconcile cannot infer the deprecation after the fact.

    Exact wording is T5's to write; the needles are S17's own terms.
    Verified absent from the manifest generated at HEAD (the manifest
    already documents OKF's ``status: draft|stable|deprecated``
    vocabulary, so a bare ``deprecated`` needle would be green and
    useless)."""
    (tmp_path / "pages").mkdir()
    write_vault_manifest(tmp_path)
    text = (tmp_path / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
    assert "unmarked" in text
    assert "reconcile" in text


# ---------------------------------------------------------------------------
# S15: regression pins — unchanged deletion behavior (green at HEAD)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_synthesis_leaves_capture_untouched(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
) -> None:
    """S15 (unchanged): deleting a synthesis never touches its capture —
    row, page, and bytes survive."""
    capture = _capture(index, tmp_vault, "Captura intacta", "Cuerpo íntegro.")
    assert capture.id is not None
    _synthesize_via_cli(
        tmp_vault, "Síntesis removida", "Cuerpo de la síntesis.", [capture.id]
    )
    synth = _synthesis_by_title(index, "Síntesis removida")
    assert synth.id is not None
    capture_bytes = _read_bytes(Path(capture.file_path))

    response = await client.delete(f"/api/v1/entries/{synth.id}")
    assert response.status_code == 204

    assert index.get_entry(synth.id) is None
    assert not _exists(Path(synth.file_path))
    assert index.get_entry(capture.id) is not None
    assert _exists(Path(capture.file_path))
    assert _read_bytes(Path(capture.file_path)) == capture_bytes


@pytest.mark.asyncio
async def test_delete_capture_still_removes_its_assets_dir(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
) -> None:
    """S15 (unchanged): deleting a capture still deletes its assets
    directory and its page file."""
    capture = _capture(index, tmp_vault, "Captura con assets", "Cuerpo íntegro.")
    assert capture.id is not None
    assets_dir = tmp_vault / "assets" / Path(capture.file_path).stem
    assets_dir.mkdir(parents=True)
    (assets_dir / "image.jpg").write_bytes(b"fake")

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204

    assert not _exists(assets_dir)
    assert not _exists(Path(capture.file_path))
    assert index.get_entry(capture.id) is None


@pytest.mark.asyncio
async def test_delete_capture_with_zero_citing_syntheses_unchanged(
    index: VaultIndex,
    tmp_vault: Path,
    client: httpx2.AsyncClient,
) -> None:
    """S15 (unchanged): deleting a capture with zero citing syntheses
    behaves exactly as before — the capture goes away and nothing else in
    the vault changes."""
    orphan = _capture(index, tmp_vault, "Captura sin cites", "Cuerpo huérfano.")
    assert orphan.id is not None
    cited = _capture(index, tmp_vault, "Captura con cite", "Cuerpo citado.")
    assert cited.id is not None
    _synthesize_via_cli(
        tmp_vault, "Síntesis ajena", "Cuerpo de la síntesis.", [cited.id]
    )
    synth = _synthesis_by_title(index, "Síntesis ajena")
    assert synth.id is not None
    cited_bytes = _read_bytes(Path(cited.file_path))
    synth_bytes = _read_bytes(Path(synth.file_path))

    response = await client.delete(f"/api/v1/entries/{orphan.id}")
    assert response.status_code == 204

    assert index.get_entry(orphan.id) is None
    assert not _exists(Path(orphan.file_path))
    cited_after = index.get_entry(cited.id)
    assert cited_after is not None
    assert _read_bytes(Path(cited.file_path)) == cited_bytes
    assert index.get_entry(synth.id) is not None
    assert _read_bytes(Path(synth.file_path)) == synth_bytes
