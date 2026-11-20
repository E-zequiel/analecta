"""T1: synthesis ``sources_basis`` staleness core (S1/S2 of okf-cli).

S2 defines a synthesis as **stale** when the current post-frontmatter body
hash of any cited source differs from its recorded ``sources_basis`` entry,
or when the file of any cited resource no longer exists.

API-name note (test-authored proposal, pending owner approval): the core
entry point proposed here is
``analecta.storage.synthesis.synthesis_stale_sources(record) -> list[str]``,
returning the ``sources[].resource`` strings that drifted (empty list =
fresh). At HEAD this import fails — the mechanism does not exist yet —
and that collection error IS the demonstrated red for S2.
"""

import hashlib
import re
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.synthesis import (
    create_synthesis_entry,
    synthesis_stale_sources,
)
from analecta.storage.vault import VaultManager


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _post_frontmatter_body_bytes(path: Path) -> bytes:
    """Post-frontmatter body bytes exactly as stored on disk (S1 identity)."""
    data = path.read_bytes()
    assert data.startswith(b"---\n")
    end = data.index(b"\n---\n") + len(b"\n---\n")
    return data[end:]


def _basis(path: Path) -> str:
    """sha256[:16] UTF-8 hexdigest of the on-disk post-frontmatter body."""
    return hashlib.sha256(_post_frontmatter_body_bytes(path)).hexdigest()[:16]


def _capture(vault: VaultManager, index: VaultIndex, n: int, body: str) -> EntryRecord:
    """Write a real capture page with frontmatter on disk and index it."""
    dt = datetime.now(tz=UTC)
    path = vault.page_path(f"Captura {n}", dt)
    path.write_text(f"---\ntitle: 'Captura {n}'\n---\n\n{body}\n", encoding="utf-8")
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


def _resource_of(entry: EntryRecord) -> str:
    return f"/pages/{Path(entry.file_path).name}"


@pytest.fixture
def env(tmp_path: Path) -> Generator[tuple[VaultIndex, VaultManager]]:
    index = VaultIndex(tmp_path / "vault.db")
    vault = VaultManager(tmp_path / "vault")
    vault.ensure_dirs()
    yield index, vault
    index.close()


def _synthesis_record(index: VaultIndex) -> EntryRecord:
    (synth,) = [r for r in index.list_entries() if r.source_type == "synthesis"]
    return synth


def test_fresh_synthesis_reports_no_stale_sources(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    index, vault = env
    capture = _capture(vault, index, 1, "Cuerpo íntegro de la captura.")
    create_synthesis_entry(
        index,
        vault,
        title="Síntesis fresca",
        body="Cuerpo de la síntesis.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    synth = _synthesis_record(index)
    assert synthesis_stale_sources(synth) == []


def test_body_edit_marks_cited_source_stale(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    index, vault = env
    capture = _capture(vault, index, 1, "Cuerpo íntegro de la captura.")
    create_synthesis_entry(
        index,
        vault,
        title="Síntesis a editar",
        body="Cuerpo de la síntesis.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    synth = _synthesis_record(index)

    page = Path(capture.file_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "Cuerpo íntegro de la captura.",
            "Cuerpo editado después de la síntesis.",
        ),
        encoding="utf-8",
    )
    assert synthesis_stale_sources(synth) == [_resource_of(capture)]


def test_frontmatter_only_edit_keeps_synthesis_fresh(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    index, vault = env
    capture = _capture(vault, index, 1, "Cuerpo íntegro de la captura.")
    create_synthesis_entry(
        index,
        vault,
        title="Síntesis tras rename",
        body="Cuerpo de la síntesis.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    synth = _synthesis_record(index)

    page = Path(capture.file_path)
    text = page.read_text(encoding="utf-8")
    # Frontmatter-only rewrite: the title moves, the body bytes do not.
    assert "title: 'Captura 1'" in text
    page.write_text(
        text.replace("title: 'Captura 1'", "title: 'Captura 1 renombrada'"),
        encoding="utf-8",
    )
    assert synthesis_stale_sources(synth) == []


def test_missing_source_file_marks_cited_source_stale(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    index, vault = env
    capture = _capture(vault, index, 1, "Cuerpo íntegro de la captura.")
    create_synthesis_entry(
        index,
        vault,
        title="Síntesis huérfana",
        body="Cuerpo de la síntesis.",
        source_entries=[capture],
        generated_by="bot/x",
    )
    synth = _synthesis_record(index)

    Path(capture.file_path).unlink()
    assert synthesis_stale_sources(synth) == [_resource_of(capture)]


def test_only_drifted_source_is_reported(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    index, vault = env
    a = _capture(vault, index, 1, "Cuerpo distinto de A.")
    b = _capture(vault, index, 2, "Cuerpo distinto de B, con ñ.")
    create_synthesis_entry(
        index,
        vault,
        title="Síntesis mixta",
        body="Cuerpo de la síntesis.",
        source_entries=[a, b],
        generated_by="bot/x",
    )
    synth = _synthesis_record(index)

    page_a = Path(a.file_path)
    page_a.write_text(
        page_a.read_text(encoding="utf-8").replace(
            "Cuerpo distinto de A.", "Cuerpo de A editado."
        ),
        encoding="utf-8",
    )
    assert synthesis_stale_sources(synth) == [_resource_of(a)]


def _write_synthesis_page(
    vault: VaultManager,
    index: VaultIndex,
    title: str,
    data: dict[str, object],
) -> EntryRecord:
    """Hand-write a synthesis-shaped page (deterministic) and index it."""
    dt = datetime.now(tz=UTC)
    path = vault.page_path(title, dt)
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem)
    markdown = (
        f"---\n{yaml.dump(data, allow_unicode=True, sort_keys=False)}---\n"
        "\nCuerpo de la síntesis.\n"
    )
    path.write_text(markdown, encoding="utf-8")
    record = EntryRecord(
        title=title,
        url=f"analecta://synthesis/{slug}",
        file_path=str(path),
        source_type="synthesis",
        created_at=_now(),
        updated_at=_now(),
    )
    record.id = index.add_entry(record)
    return record


def _frontmatter_data(path: Path) -> dict[str, object]:
    """Parse the YAML frontmatter dict of a page (mirrors FM_BLOCK)."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text[4 : text.index("\n---\n")])


def test_synthesis_without_sources_basis_is_stale(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c: legacy synthesis (sources[], no sources_basis) is stale."""
    index, vault = env
    capture = _capture(vault, index, 1, "Cuerpo íntegro de la captura.")
    resource = _resource_of(capture)
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis legado",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [{"resource": resource, "title": capture.title}],
            # No sources_basis key: unverifiable legacy frontmatter.
        },
    )
    assert synthesis_stale_sources(record) == [resource]


def test_sources_entry_without_matching_basis_is_stale(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c sanity rule: sources[] entries lacking a matching
    sources_basis resource are reported stale; basis-backed fresh sources
    are not."""
    index, vault = env
    covered = _capture(vault, index, 1, "Cuerpo de la captura cubierta.")
    uncovered = _capture(vault, index, 2, "Cuerpo de la captura sin basis.")
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis con basis parcial",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [
                {"resource": _resource_of(covered), "title": covered.title},
                {"resource": _resource_of(uncovered), "title": uncovered.title},
            ],
            "sources_basis": [
                {
                    "resource": _resource_of(covered),
                    "basis": _basis(Path(covered.file_path)),
                },
            ],
        },
    )
    stale = synthesis_stale_sources(record)
    assert _resource_of(covered) not in stale
    assert stale == [_resource_of(uncovered)]


def test_missing_capture_at_synthesis_time_records_empty_basis(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """S1 boundary: a capture whose file is missing at synthesis time gets
    basis ``"" in sources_basis; once the file appears, it is stale."""
    index, vault = env
    missing_path = vault.pages_path / "2099-01-01-captura-desaparecida.md"
    ghost = EntryRecord(
        title="Captura desaparecida",
        url="https://example.com/captura-desaparecida",
        file_path=str(missing_path),
        source_type="article",
        created_at=_now(),
        updated_at=_now(),
    )
    create_synthesis_entry(
        index,
        vault,
        title="Síntesis de fantasma",
        body="Cuerpo de la síntesis.",
        source_entries=[ghost],
        generated_by="bot/x",
    )
    synth = _synthesis_record(index)

    resource = "/pages/2099-01-01-captura-desaparecida.md"
    data = _frontmatter_data(Path(synth.file_path))
    assert data["sources_basis"] == [{"resource": resource, "basis": ""}]

    missing_path.write_text(
        "---\ntitle: 'Captura desaparecida'\n---\n\n"
        "Cuerpo de la captura, escrito después de la síntesis.\n",
        encoding="utf-8",
    )
    assert synthesis_stale_sources(synth) == [resource]


def test_empty_sources_basis_list_is_stale_wholesale(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c: sources_basis present but empty is stale wholesale."""
    index, vault = env
    a = _capture(vault, index, 1, "Cuerpo A.")
    b = _capture(vault, index, 2, "Cuerpo B, con ñ.")
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis basis vacía",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [
                {"resource": _resource_of(a)},
                {"resource": _resource_of(b)},
            ],
            "sources_basis": [],
        },
    )
    assert synthesis_stale_sources(record) == [_resource_of(a), _resource_of(b)]


def test_malformed_sources_basis_is_stale_wholesale(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c: non-list or all-non-dict sources_basis is unverifiable."""
    index, vault = env
    a = _capture(vault, index, 1, "Cuerpo A.")
    resource = _resource_of(a)
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis basis malformada",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [{"resource": resource}],
            "sources_basis": "oops",
        },
    )
    assert synthesis_stale_sources(record) == [resource]
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis basis no dict",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [{"resource": resource}],
            "sources_basis": ["x", None],
        },
    )
    assert synthesis_stale_sources(record) == [resource]


def test_non_str_basis_entry_is_uncovered_and_stale(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c: a basis entry with a non-string basis cannot verify."""
    index, vault = env
    a = _capture(vault, index, 1, "Cuerpo A.")
    resource = _resource_of(a)
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis basis no str",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [{"resource": resource}],
            "sources_basis": [{"resource": resource, "basis": None}],
        },
    )
    assert synthesis_stale_sources(record) == [resource]


def test_duplicate_sources_entry_is_reported_once(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c: a duplicated drifted source is reported exactly once."""
    index, vault = env
    a = _capture(vault, index, 1, "Cuerpo A.")
    resource = _resource_of(a)
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis duplicada",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [{"resource": resource}, {"resource": resource}],
            "sources_basis": [{"resource": resource, "basis": "0000000000000000"}],
        },
    )
    assert synthesis_stale_sources(record) == [resource]


def test_basis_only_entry_outside_sources_keeps_original_check(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c: basis entries outside sources[] keep hash/missing-file."""
    index, vault = env
    a = _capture(vault, index, 1, "Cuerpo A.")
    extra = _capture(vault, index, 2, "Cuerpo extra, con ñ.")
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis basis extra",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [{"resource": _resource_of(a)}],
            "sources_basis": [
                {"resource": _resource_of(a), "basis": _basis(Path(a.file_path))},
                {"resource": _resource_of(extra), "basis": "1111111111111111"},
            ],
        },
    )
    assert synthesis_stale_sources(record) == [_resource_of(extra)]
    Path(extra.file_path).unlink()
    assert synthesis_stale_sources(record) == [_resource_of(extra)]


def test_non_list_sources_is_fresh(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c: a malformed non-list sources is not nonempty sources[]."""
    index, vault = env
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis sources malformadas",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": "not-a-list",
        },
    )
    assert synthesis_stale_sources(record) == []


def test_duplicate_basis_entries_last_wins_without_double_report(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """Amended S2c: conflicting duplicate basis entries cover, never double."""
    index, vault = env
    a = _capture(vault, index, 1, "Cuerpo A.")
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis basis duplicada",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [{"resource": _resource_of(a)}],
            "sources_basis": [
                {"resource": _resource_of(a), "basis": "2222222222222222"},
                {"resource": _resource_of(a), "basis": _basis(Path(a.file_path))},
            ],
        },
    )
    assert synthesis_stale_sources(record) == []


# ---------------------------------------------------------------------------
# T5 S15: ``..``-escaping resources are stale without reading the file
# ---------------------------------------------------------------------------


def test_resource_escaping_pages_dir_is_stale_without_reading_it(
    env: tuple[VaultIndex, VaultManager],
) -> None:
    """S15: a cited ``resource`` carrying ``..`` segments that escapes the
    vault's ``pages/`` directory is reported stale, and its file is never
    read.

    Design of the red: both target files exist and are well-formed pages
    whose post-frontmatter body hash exactly matches the ``basis`` recorded
    in ``sources_basis`` — if the implementation read and hashed them, they
    would verify fresh. Asserting staleness therefore proves containment:
    the escape is rejected on the resource string alone, with no read of
    the file outside ``pages/`` (the deeper escape resolves outside the
    vault root entirely). A mid-crash atomicity test is out of scope here;
    this pins the containment decision of ``synthesis_stale_sources``.
    """
    index, vault = env
    outside_dir = vault.pages_path.parent / "outside"
    deep_dir = vault.pages_path.parent.parent / "outside-deep"
    outside_dir.mkdir(parents=True)
    deep_dir.mkdir(parents=True)
    shallow_file = outside_dir / "secret.md"
    deep_file = deep_dir / "secret.md"
    shallow_resource = "/pages/../outside/secret.md"
    deep_resource = "/pages/../../outside-deep/secret.md"
    for path in (shallow_file, deep_file):
        path.write_text(
            "---\ntitle: 'Secret'\n---\n\nCuerpo secreto fuera de pages/.\n",
            encoding="utf-8",
        )
    record = _write_synthesis_page(
        vault,
        index,
        "Síntesis escape",
        {
            "type": "synthesis",
            "generated": {"by": "bot/x", "at": _now()},
            "sources": [
                {"resource": shallow_resource},
                {"resource": deep_resource},
            ],
            "sources_basis": [
                {"resource": shallow_resource, "basis": _basis(shallow_file)},
                {"resource": deep_resource, "basis": _basis(deep_file)},
            ],
        },
    )
    assert synthesis_stale_sources(record) == [shallow_resource, deep_resource]
