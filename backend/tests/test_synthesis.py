"""T3: synthesis entries are first-class vault citizens.

Owner decision D10: synthesis concepts live in ``pages/`` with the synthetic
URL ``analecta://synthesis/<slug>``, are indexed by the app (FTS, backlinks,
graph), and are distinguishable from captures by ``type: synthesis`` (file
frontmatter) and ``source_type='synthesis'`` (DB discriminator). Full spec
pins: ``~/Downloads/analecta-okf-cli-analisis.md``.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.synthesis import create_synthesis_entry
from analecta.storage.vault import VaultManager


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


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
    assert page.exists()
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
