"""T2: CLI synthesis staleness surfaces (S3/S4 of okf-cli).

S3 — ``analecta synthesize --stale`` (no creation arguments) prints a
staleness report and exits 1 when at least one synthesis is stale, 0
otherwise. Pinned report contract (human output):

- Every stale synthesis is named on its own line, together with the word
  ``stale`` and the exact vault-relative resource(s) that made it stale
  (``/pages/<file>.md`` — the ``sources[].resource`` strings).
- Fresh syntheses are confirmed: their titles appear in the report, not
  on any stale line, and the report states the fresh count (``N fresh``)
  alongside the stale count (``M stale``).
- Captures (references) are never reported: only synthesis entries are
  classified, and a vault with no syntheses at all exits 0 with a
  nonempty message naming no ``/pages/`` resource as stale.

S4 — ``analecta index`` visibly marks stale synthesis entries in its
human listing by appending `` — STALE`` to the exact current listing
line (``- [Title](/pages/<file>.md) — STALE``). Fresh syntheses and
reference captures carry no marker. The index command itself still
exits 0 regardless of staleness.

Scenarios are built through the real CLI: captures and syntheses are
created via ``run([...])`` against an injected vault (the standalone
mode the tests exercise is forced by patching the attached-mode
discovery lookup to ``None``), staleness is produced by editing capture
bodies after synthesis or by hand-writing legacy (sources[]-without-
sources_basis) synthesis pages. No network calls.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from analecta.cli import run
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Empty vault root (pages/ and assets/ created)."""
    vault = VaultManager(tmp_path / "vault")
    vault.ensure_dirs()
    return tmp_path / "vault"


@pytest.fixture
def standalone_only(mocker: MockerFixture) -> None:
    """Force ``index``/``log`` into standalone mode (no live sidecar).

    The attached-mode discovery lookup would talk to whatever sidecar is
    running on this machine, ignoring ``vault_path``; neutralize it so
    the CLI reads the injected test vault.
    """
    mocker.patch("analecta.cli._attached_get", return_value=None)


def _capture(vault_dir: Path, index: VaultIndex, title: str, body: str) -> EntryRecord:
    """Write a real capture page on disk and index it."""
    dt = datetime.now(tz=UTC)
    path = VaultManager(vault_dir).page_path(title, dt)
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


def _open_index(vault_dir: Path) -> VaultIndex:
    return VaultIndex(vault_dir / "analecta.db")


def _synthesize_via_cli(
    vault_dir: Path, title: str, body: str, source_ids: list[int]
) -> None:
    """Create a synthesis through the CLI surface (records sources_basis)."""
    argv = ["synthesize", "--title", title, "--body", body, "--by", "bot/x"]
    for sid in source_ids:
        argv += ["--source", str(sid)]
    assert run(argv, vault_path=vault_dir) == 0


def _sole_synthesis(vault_dir: Path) -> EntryRecord:
    index = _open_index(vault_dir)
    try:
        (synth,) = [r for r in index.list_entries() if r.source_type == "synthesis"]
    finally:
        index.close()
    return synth


def _resource_of(entry: EntryRecord) -> str:
    return f"/pages/{Path(entry.file_path).name}"


def _edit_body(page: Path, old: str, new: str) -> None:
    """Edit a capture's body after synthesis (post-frontmatter drift)."""
    page.write_text(
        page.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )


def _write_legacy_synthesis(
    vault_dir: Path,
    index: VaultIndex,
    title: str,
    resources: list[str],
) -> EntryRecord:
    """Hand-write a legacy synthesis page (sources[], no sources_basis)."""
    dt = datetime.now(tz=UTC)
    path = VaultManager(vault_dir).page_path(title, dt)
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem)
    data: dict[str, object] = {
        "type": "synthesis",
        "title": title,
        "url": f"analecta://synthesis/{slug}",
        "source_type": "synthesis",
        "created_at": _now(),
        "generated": {"by": "bot/x", "at": _now()},
        "sources": [{"resource": r} for r in resources],
    }
    path.write_text(
        f"---\n{yaml.dump(data, allow_unicode=True, sort_keys=False)}---\n"
        "\nCuerpo de la síntesis legada.\n",
        encoding="utf-8",
    )
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


def _stale_lines(out: str) -> list[str]:
    """Lines of the report the word ``stale`` appears on."""
    return [ln for ln in out.splitlines() if "stale" in ln.lower()]


def _lines_named(out: str, title: str) -> list[str]:
    return [ln for ln in out.splitlines() if title in ln]


# ---------------------------------------------------------------------------
# S3: ``synthesize --stale`` report
# ---------------------------------------------------------------------------


def test_stale_report_all_fresh_exits_zero(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    index = _open_index(vault_dir)
    try:
        capture = _capture(
            vault_dir,
            index,
            "Captura fresca",
            "Cuerpo íntegro de la captura.",
        )
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis fresca", "Cuerpo.", [capture.id])

    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 0
    # The fresh synthesis is confirmed by name.
    assert "Síntesis fresca" in out
    # ...and the fresh count is stated.
    assert "1 fresh" in out
    # ...without being reported stale.
    assert not any("stale" in ln.lower() for ln in _lines_named(out, "Síntesis fresca"))


def test_stale_report_names_drifted_resource_exits_one(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    index = _open_index(vault_dir)
    try:
        a = _capture(vault_dir, index, "Captura A", "Cuerpo original de A.")
        b = _capture(vault_dir, index, "Captura B", "Cuerpo original de B, con ñ.")
    finally:
        index.close()
    assert a.id is not None
    assert b.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis mezclada", "Cuerpo.", [a.id, b.id])

    # Drift A only; B stays untouched.
    _edit_body(Path(a.file_path), "Cuerpo original de A.", "Cuerpo editado de A.")

    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 1
    named = [ln for ln in _stale_lines(out) if "Síntesis mezclada" in ln]
    assert named, f"stale synthesis not named on a stale line: {out!r}"
    # The exact drifted resource is named on the synthesis's stale line.
    assert any(_resource_of(a) in ln for ln in named)
    # The still-fresh source is not reported as stale.
    assert not any(_resource_of(b) in ln for ln in named)


def test_stale_report_legacy_synthesis_without_sources_basis_exits_one(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    index = _open_index(vault_dir)
    try:
        capture = _capture(
            vault_dir, index, "Captura citada", "Cuerpo íntegro de la captura."
        )
        resource = _resource_of(capture)
        _write_legacy_synthesis(vault_dir, index, "Síntesis legado", [resource])
    finally:
        index.close()

    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    out = capsys.readouterr().out

    # Legacy frontmatter is unverifiable → stale wholesale (S2c).
    assert code == 1
    named = [ln for ln in _stale_lines(out) if "Síntesis legado" in ln]
    assert named, f"legacy synthesis not named on a stale line: {out!r}"
    assert any(resource in ln for ln in named)


def test_stale_report_lists_every_stale_synthesis_and_counts(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    index = _open_index(vault_dir)
    try:
        a = _capture(vault_dir, index, "Captura A", "Cuerpo A.")
        b = _capture(vault_dir, index, "Captura B", "Cuerpo B.")
        c = _capture(vault_dir, index, "Captura C", "Cuerpo C.")
    finally:
        index.close()
    assert a.id is not None
    assert c.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis A", "Cuerpo.", [a.id])
    index = _open_index(vault_dir)
    try:
        _write_legacy_synthesis(vault_dir, index, "Síntesis B", [_resource_of(b)])
    finally:
        index.close()
    _synthesize_via_cli(vault_dir, "Síntesis C", "Cuerpo.", [c.id])

    # Drift A only: B is legacy-stale, C stays fresh.
    _edit_body(Path(a.file_path), "Cuerpo A.", "Cuerpo A editado.")

    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 1
    # Every stale synthesis is named with its exact drifted resource.
    named_a = [ln for ln in _stale_lines(out) if "Síntesis A" in ln]
    named_b = [ln for ln in _stale_lines(out) if "Síntesis B" in ln]
    assert any(_resource_of(a) in ln for ln in named_a)
    assert any(_resource_of(b) in ln for ln in named_b)
    # Fresh synthesis confirmed by name, not on any stale line.
    assert "Síntesis C" in out
    assert not any("stale" in ln.lower() for ln in _lines_named(out, "Síntesis C"))
    # The counts are stated: 2 stale, 1 fresh.
    assert "2 stale" in out
    assert "1 fresh" in out


def test_stale_report_never_reports_captures(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    index = _open_index(vault_dir)
    try:
        cited = _capture(vault_dir, index, "Captura Citada", "Cuerpo citado.")
        _capture(vault_dir, index, "Captura Suelta", "Cuerpo suelto.")
    finally:
        index.close()
    assert cited.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis única", "Cuerpo.", [cited.id])

    _edit_body(Path(cited.file_path), "Cuerpo citado.", "Cuerpo citado editado.")

    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 1
    named = [ln for ln in _stale_lines(out) if "Síntesis única" in ln]
    assert named
    # Only the synthesis is classified: the capture titles never appear
    # on a stale line, even the drifted one's own capture.
    assert not any("Captura Citada" in ln for ln in _stale_lines(out))
    assert not any("Captura Suelta" in ln for ln in _stale_lines(out))


def test_stale_report_empty_vault_exits_zero_without_crash(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    index = _open_index(vault_dir)
    try:
        _capture(vault_dir, index, "Captura sola", "Cuerpo sin síntesis.")
    finally:
        index.close()

    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 0
    # A sane (nonempty) message, naming no resource as stale.
    assert out.strip() != ""
    assert "/pages/" not in out


# ---------------------------------------------------------------------------
# S4: ``analecta index`` stale marking
# ---------------------------------------------------------------------------


def test_index_marks_stale_synthesis_line(
    vault_dir: Path, capsys: pytest.CaptureFixture[str], standalone_only: None
) -> None:
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura índice", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis índice", "Cuerpo.", [capture.id])

    _edit_body(Path(capture.file_path), "Cuerpo íntegro.", "Cuerpo editado.")

    code = run(["index"], vault_path=vault_dir)
    out = capsys.readouterr().out

    # The index command itself still succeeds.
    assert code == 0
    synth = _sole_synthesis(vault_dir)
    expected = f"- [{synth.title}]({_resource_of(synth)}) — STALE"
    lines = out.splitlines()
    # Exact marker shape against the real current listing line.
    assert expected in lines
    # ...inside the Synthesis section, not the Library.
    synth_section = out.split("# Synthesis", 1)[1]
    assert expected in synth_section.splitlines()


def test_index_leaves_fresh_synthesis_and_references_unmarked(
    vault_dir: Path, capsys: pytest.CaptureFixture[str], standalone_only: None
) -> None:
    index = _open_index(vault_dir)
    try:
        cited = _capture(vault_dir, index, "Captura citada", "Cuerpo citado.")
        _capture(vault_dir, index, "Captura no citada", "Otro cuerpo.")
    finally:
        index.close()
    assert cited.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis impecable", "Cuerpo.", [cited.id])

    code = run(["index"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 0
    # No marker anywhere: fresh syntheses and references stay clean.
    assert "STALE" not in out
    synth = _sole_synthesis(vault_dir)
    expected = f"- [{synth.title}]({_resource_of(synth)})"
    lines = out.splitlines()
    assert expected in lines
    assert "- [Captura no citada](/pages/" in out


def test_index_marks_legacy_stale_synthesis(
    vault_dir: Path, capsys: pytest.CaptureFixture[str], standalone_only: None
) -> None:
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura legado", "Cuerpo íntegro.")
        resource = _resource_of(capture)
        record = _write_legacy_synthesis(
            vault_dir, index, "Síntesis legado índice", [resource]
        )
    finally:
        index.close()

    code = run(["index"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 0
    expected = f"- [{record.title}]({_resource_of(record)}) — STALE"
    lines = out.splitlines()
    assert expected in lines
    synth_section = out.split("# Synthesis", 1)[1]
    assert expected in synth_section.splitlines()
