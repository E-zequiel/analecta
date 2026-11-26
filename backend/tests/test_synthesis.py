"""T3: synthesis entries are first-class vault citizens.

Owner decision D10: synthesis concepts live in ``pages/`` with the synthetic
URL ``analecta://synthesis/<slug>``, are indexed by the app (FTS, backlinks,
graph), and are distinguishable from captures by ``type: synthesis`` (file
frontmatter) and ``source_type='synthesis'`` (DB discriminator). Full spec
pins: ``~/Downloads/analecta-okf-cli-analisis.md``.
"""

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
import yaml
from fastapi import FastAPI

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.config import AppConfig
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.synthesis import create_synthesis_entry
from analecta.storage.vault import VaultManager


def _exists(path: Path) -> bool:
    """Sync seam for ASYNC240: blocking fs probe callable from async tests."""
    return path.exists()


def _read_bytes(path: Path) -> bytes:
    """Sync seam for ASYNC240: blocking read callable from async tests."""
    return path.read_bytes()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


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


def _capture(index: VaultIndex, *, n: int = 1) -> EntryRecord:
    entry = EntryRecord(
        title=f"Capture {n}",
        url=f"https://example.com/capture-{n}",
        file_path=f"/vault/pages/2026-05-27-capture-{n}.md",
        source_type="article",
        created_at=_now(),
        updated_at=_now(),
    )
    entry.id = index.add_entry(entry)
    return entry


@pytest.fixture
def env(
    tmp_path: Path,
) -> Generator[tuple[VaultIndex, VaultManager]]:
    index = VaultIndex(tmp_path / "vault.db")
    vault = VaultManager(tmp_path / "vault")
    vault.ensure_dirs()
    yield index, vault
    index.close()


def test_create_synthesis_entry_roundtrip(env: tuple[VaultIndex, VaultManager]) -> None:
    index, vault = env
    capture = _capture(index)
    eid = create_synthesis_entry(
        index,
        vault,
        title="Síntesis de Capture 1",
        body="Una síntesis que [[Capture 1]] explica y #aclara.",
        source_entries=[capture],
        generated_by="bot/gemini-2.5-pro",
    )

    record = index.get_entry(eid)
    assert record is not None
    assert record.source_type == "synthesis"
    assert record.read_state == "unread"
    assert record.url == "analecta://synthesis/sintesis-de-capture-1"

    page = Path(record.file_path)
    assert _exists(page)
    assert "Body text" not in page.read_text(encoding="utf-8")
    inner = page.read_text(encoding="utf-8").split("---\n", 2)[1]
    import yaml

    data = yaml.safe_load(inner)
    assert data["type"] == "synthesis"
    assert data["source_type"] == "synthesis"
    assert data["generated"] == {
        "by": "bot/gemini-2.5-pro",
        "at": record.created_at,
    }
    (source,) = data["sources"]
    assert source["resource"] == "/pages/2026-05-27-capture-1.md"
    assert source["title"] == "Capture 1"
    assert "Una síntesis que [[Capture 1]] explica" in page.read_text(encoding="utf-8")


def test_synthesis_url_accepted_by_plain_add_entry(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """No validator may reject the synthetic scheme (D10 schema pin)."""
    index, _ = env
    eid = index.add_entry(
        EntryRecord(
            title="Direct synthetic",
            url="analecta://synthesis/direct-synthetic",
            file_path="/vault/pages/2026-06-01-direct-synthetic.md",
            source_type="synthesis",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    record = index.get_entry(eid)
    assert record is not None
    assert record.url == "analecta://synthesis/direct-synthetic"


def test_synthesis_is_fts_searchable(env: tuple[VaultIndex, VaultManager]) -> None:
    index, vault = env
    capture = _capture(index)
    create_synthesis_entry(
        index,
        vault,
        title="Síntesis búsqueda",
        body="Palabra distintiva quokka dentro del cuerpo.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    hits = index.search("quokka")
    assert len(hits) == 1


def test_synthesis_backlinks_are_indexed(env: tuple[VaultIndex, VaultManager]) -> None:
    index, vault = env
    capture = _capture(index)
    eid = create_synthesis_entry(
        index,
        vault,
        title="Síntesis con wikilink",
        body="Véase [[Capture 1]] para el original.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    refs = index._conn.execute(
        "SELECT target_text, is_hashtag FROM backlink_refs WHERE source_id = ?",
        (eid,),
    ).fetchall()
    assert (capture.title.lower(), 0) in [
        (r["target_text"], r["is_hashtag"]) for r in refs
    ]


def test_synthesis_duplicate_slug_conflicts(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    import sqlite3

    index, vault = env
    capture = _capture(index)
    kwargs: dict[str, object] = dict(
        index=index,
        vault=vault,
        title="Síntesis duplicada",
        body="Primera.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    create_synthesis_entry(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):
        create_synthesis_entry(**kwargs)  # type: ignore[arg-type]


def test_synthesis_page_naming_uses_creation_date(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    index, vault = env
    capture = _capture(index)
    create_synthesis_entry(
        index,
        vault,
        title="Con fecha",
        body="Cuerpo.",
        source_entries=[capture],
        generated_by="bot/x",
        created_at="2026-11-18T09:00:00+00:00",
    )
    page = next(iter(vault.pages_path.glob("*con-fecha.md")))
    assert page.name.startswith("2026-11-18-")


def test_synthesis_counts_as_synthesis_not_capture(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    index, vault = env
    capture = _capture(index)
    create_synthesis_entry(
        index,
        vault,
        title="Clasificación",
        body="Cuerpo.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    source_types = {r.source_type for r in index.list_entries()}
    assert source_types == {"article", "synthesis"}


# ---------------------------------------------------------------------------
# T1/T4: capture deletion deprecates citing syntheses (D13 → OKF
# deprecation, S1/S15/S16)
#
# In-place replacement of the D13 cascade pins. The tests reference the
# NEW behavior through the public DELETE /api/v1/entries/{id} surface —
# never the storage deletion helper — so the S16 rename
# (delete_syntheses_for_entry → deprecate_syntheses_for_entry) cannot
# require test edits. Red at HEAD: the cascade deletes the citing
# synthesis (row and page). The unidirectional pin is green at HEAD by
# design (S15, unchanged behavior).
# ---------------------------------------------------------------------------


def _real_capture(index: VaultIndex, vault: VaultManager, n: int) -> EntryRecord:
    """A capture with a real on-disk page (the API delete removes it)."""
    vault.ensure_dirs()
    path = vault.page_path(f"Captura {n}", datetime.now(tz=UTC))
    path.write_text(
        f"---\ntitle: 'Captura {n}'\n---\n\nCuerpo íntegro de la captura {n}.\n",
        encoding="utf-8",
    )
    entry = EntryRecord(
        title=f"Captura {n}",
        url=f"https://example.com/captura-{n}",
        file_path=str(path),
        source_type="article",
        created_at=_now(),
        updated_at=_now(),
    )
    entry.id = index.add_entry(entry)
    return entry


def _frontmatter_of(path: Path) -> dict[str, object]:
    """Parse the YAML frontmatter dict of a page."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    data = yaml.safe_load(text[4 : text.index("\n---\n")])
    assert isinstance(data, dict)
    return data


def _body_bytes(path: Path) -> bytes:
    """Post-frontmatter body bytes exactly as stored on disk."""
    data = path.read_bytes()
    assert data.startswith(b"---\n")
    end = data.index(b"\n---\n") + len(b"\n---\n")
    return data[end:]


def _synthesis_page(index: VaultIndex, eid: int) -> Path:
    record = index.get_entry(eid)
    assert record is not None
    return Path(record.file_path)


@pytest.mark.asyncio
async def test_delete_capture_deprecates_citing_synthesis(
    index: VaultIndex,
    vault: VaultManager,
    client: httpx2.AsyncClient,
) -> None:
    """S1 (in-place replacement of the old cascade pin): deleting a
    capture no longer deletes the citing synthesis. The synthesis
    survives with ``status: deprecated`` in its frontmatter, every other
    frontmatter key preserved, the body untouched, and its DB row
    intact."""
    capture = _real_capture(index, vault, 1)
    assert capture.id is not None
    eid = create_synthesis_entry(
        index,
        vault,
        title="Síntesis en deprecación",
        body="Cuerpo que cita la fuente.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    synth = index.get_entry(eid)
    assert synth is not None
    page = Path(synth.file_path)
    pre_fm = _frontmatter_of(page)
    pre_body = _body_bytes(page)

    response = await client.delete(f"/api/v1/entries/{capture.id}")
    assert response.status_code == 204

    # DB row preserved.
    synth_after = index.get_entry(eid)
    assert synth_after is not None
    assert synth_after.id == eid
    assert synth_after.url == synth.url
    assert synth_after.file_path == synth.file_path
    assert synth_after.source_type == "synthesis"
    assert synth_after.title == synth.title
    assert synth_after.created_at == synth.created_at
    # Page survives: status: deprecated added, everything else preserved,
    # body untouched.
    assert _exists(page)
    assert _frontmatter_of(page) == {**pre_fm, "status": "deprecated"}
    assert _body_bytes(page) == pre_body
    # The capture itself is gone.
    assert index.get_entry(capture.id) is None
    assert not _exists(Path(capture.file_path))


@pytest.mark.asyncio
async def test_deprecation_marks_only_citing_syntheses(
    index: VaultIndex,
    vault: VaultManager,
    client: httpx2.AsyncClient,
) -> None:
    """S1 (in-place replacement of the selective-cascade pin): deleting a
    capture deprecates only the syntheses citing it; a synthesis citing
    another capture is untouched — page bytes identical, no ``status``
    key."""
    a = _real_capture(index, vault, 1)
    b = _real_capture(index, vault, 2)
    keep = create_synthesis_entry(
        index,
        vault,
        title="Síntesis de A",
        body="Cita a [[Captura 1]].",
        source_entries=[a],
        generated_by="bot/x",
    )
    other = create_synthesis_entry(
        index,
        vault,
        title="Síntesis de B",
        body="Cita a [[Captura 2]].",
        source_entries=[b],
        generated_by="bot/x",
    )
    other_page = _synthesis_page(index, other)
    other_bytes = _read_bytes(other_page)

    response = await client.delete(f"/api/v1/entries/{a.id}")
    assert response.status_code == 204

    keep_page = _synthesis_page(index, keep)
    assert _frontmatter_of(keep_page)["status"] == "deprecated"
    assert index.get_entry(other) is not None
    assert _read_bytes(other_page) == other_bytes


@pytest.mark.asyncio
async def test_deleting_synthesis_leaves_capture_alone(
    index: VaultIndex,
    vault: VaultManager,
    client: httpx2.AsyncClient,
) -> None:
    """S15 (unchanged, green at HEAD): unidirectional — deleting a
    synthesis never touches its capture (row, page, and bytes
    survive)."""
    capture = _real_capture(index, vault, 1)
    assert capture.id is not None
    synth_id = create_synthesis_entry(
        index,
        vault,
        title="Síntesis removible",
        body="Cuerpo.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    synth = index.get_entry(synth_id)
    assert synth is not None
    capture_bytes = _read_bytes(Path(capture.file_path))

    response = await client.delete(f"/api/v1/entries/{synth_id}")
    assert response.status_code == 204

    assert index.get_entry(synth_id) is None
    assert not _exists(Path(synth.file_path))
    assert index.get_entry(capture.id) is not None
    assert _exists(Path(capture.file_path))
    assert _read_bytes(Path(capture.file_path)) == capture_bytes


@pytest.mark.asyncio
async def test_deprecation_marks_multi_source_synthesis(
    index: VaultIndex,
    vault: VaultManager,
    client: httpx2.AsyncClient,
) -> None:
    """S1 (in-place replacement of the multi-source cascade pin):
    deleting ANY cited capture deprecates the whole multi-source
    synthesis — it survives, marked; the other cited capture is
    untouched."""
    a = _real_capture(index, vault, 1)
    b = _real_capture(index, vault, 2)
    synth_id = create_synthesis_entry(
        index,
        vault,
        title="Síntesis multifuente",
        body="Cita a [[Captura 1]] y a [[Captura 2]].",
        source_entries=[a, b],
        generated_by="bot/x",
    )
    assert b.id is not None

    response = await client.delete(f"/api/v1/entries/{a.id}")
    assert response.status_code == 204

    page = _synthesis_page(index, synth_id)
    assert _frontmatter_of(page)["status"] == "deprecated"
    assert index.get_entry(synth_id) is not None
    assert index.get_entry(b.id) is not None
    assert _exists(Path(b.file_path))
