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

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from analecta.cli import run
from analecta.session_security import VaultLock
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


# ---------------------------------------------------------------------------
# T2b S9: ``--stale`` with the vault lock held → distinct exit code 3
# ---------------------------------------------------------------------------


def test_stale_report_when_vault_locked_exits_three(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S9: a held lock is reported on stderr and exits 3, never 1.

    Exit 1 is reserved for ``at least one synthesis is stale``; a lock
    failure must not masquerade as that result. The lock is held by a
    real ``VaultLock`` acquired inside the test (same-process second
    open + ``LOCK_EX|LOCK_NB`` fails, per ``test_vault_lock_second_writer_fails``).
    """
    holder = VaultLock(vault_dir).acquire()
    assert holder is not None
    try:
        code = run(["synthesize", "--stale"], vault_path=vault_dir)
        captured = capsys.readouterr()
    finally:
        VaultLock(vault_dir).release(holder)

    assert code == 3
    # The lock error goes to stderr, naming the conflict.
    assert "error" in captured.err
    assert "locked" in captured.err
    # No staleness report is produced on stdout.
    assert captured.out == ""


# ---------------------------------------------------------------------------
# T2b S10: ``--stale`` with --json / creation args rejected explicitly
# ---------------------------------------------------------------------------


def _sole_stale_synthesis(vault_dir: Path, capture_title: str) -> EntryRecord:
    """Create one synthesis via the CLI and drift its source; return the capture."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, capture_title, "Cuerpo original íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis condicional", "Cuerpo.", [capture.id])
    _edit_body(Path(capture.file_path), "Cuerpo original íntegro.", "Cuerpo editado.")
    return capture


def test_stale_with_json_is_rejected_explicitly(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S10: ``--stale --json`` is an explicit rejection, not a silent ignore.

    Today ``--json`` is silently ignored (the human report is printed and
    the staleness exit code is returned); it must instead fail with an
    error naming the conflict, print no report, and create nothing.
    """
    _sole_stale_synthesis(vault_dir, "Captura json")
    # Drain the setup's own output so only the invocation under test is
    # observed (the setup legitimately prints its creation lines).
    capsys.readouterr()

    code = run(["synthesize", "--stale", "--json"], vault_path=vault_dir)
    captured = capsys.readouterr()

    assert code != 0
    # The error names the conflicting flags...
    assert "--stale" in captured.err
    assert "--json" in captured.err
    # ...goes to stderr, and no report is printed on stdout.
    assert captured.out == ""
    # Nothing was created: still exactly the one synthesis.
    index = _open_index(vault_dir)
    try:
        synths = [r for r in index.list_entries() if r.source_type == "synthesis"]
    finally:
        index.close()
    assert len(synths) == 1


def test_stale_with_creation_args_is_rejected_explicitly(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S10 companion: ``--stale`` plus creation arguments is rejected too.

    A complete creation argument set alongside ``--stale`` must not be
    silently ignored (today the report runs and the creation args are
    dropped on the floor); it must fail naming the conflict, print no
    report, and create no synthesis.
    """
    capture = _sole_stale_synthesis(vault_dir, "Captura creación")
    source_id = capture.id
    assert source_id is not None
    # Drain the setup's own output so only the invocation under test is
    # observed (the setup legitimately prints its creation lines).
    capsys.readouterr()

    code = run(
        [
            "synthesize",
            "--stale",
            "--title",
            "Síntesis fantasma",
            "--body",
            "Cuerpo fantasma.",
            "--by",
            "bot/x",
            "--source",
            str(source_id),
        ],
        vault_path=vault_dir,
    )
    captured = capsys.readouterr()

    assert code != 0
    # The error names the conflicting flags...
    assert "--stale" in captured.err
    assert "--title" in captured.err
    # ...goes to stderr, and no report is printed on stdout.
    assert captured.out == ""
    # No creation happened: no second synthesis, no ghost page.
    index = _open_index(vault_dir)
    try:
        synths = [r for r in index.list_entries() if r.source_type == "synthesis"]
    finally:
        index.close()
    assert len(synths) == 1
    assert "Síntesis fantasma" not in captured.out


def test_stale_with_empty_string_creation_values_is_rejected(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S10 edge: empty-string values are still passed creation arguments.

    ``--title ''`` is truthiness-falsy, so the conflict check must not
    rely on truthiness: the invocation must be rejected with exit 2,
    never run the report with a success exit.
    """
    for flag in ("--title", "--body", "--by"):
        code = run(["synthesize", "--stale", flag, ""], vault_path=vault_dir)
        captured = capsys.readouterr()
        assert code == 2, f"--stale {flag} '' exited {code}: {captured.out!r}"
        assert "--stale" in captured.err
        assert flag in captured.err
        assert captured.out == ""


# ---------------------------------------------------------------------------
# T5 S16: missing/unreadable synthesis page is stale, fail-safe
# ---------------------------------------------------------------------------


def test_stale_report_missing_synthesis_page_is_stale_not_fresh(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S16: a synthesis whose own page file is deleted is reported stale —
    naming the synthesis's own page resource — with no traceback, exit 1.

    Fail-safe parity with S2c verifiability: an unverifiable page is not
    "fresh". Today the report classifies it fresh (an unreadable page's
    frontmatter yields no stale sources) — that is the demonstrated red.
    """
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura huérfana", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis sin página", "Cuerpo.", [capture.id])
    synth = _sole_synthesis(vault_dir)
    Path(synth.file_path).unlink()

    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    captured = capsys.readouterr()

    assert code == 1
    named = [ln for ln in _stale_lines(captured.out) if "Síntesis sin página" in ln]
    assert named, f"missing-page synthesis not named on a stale line: {captured.out!r}"
    # The synthesis's OWN page resource is named, not a cited source's.
    assert any(_resource_of(synth) in ln for ln in named)
    # Fail-safe: never a traceback.
    assert "Traceback" not in captured.out + captured.err


def test_index_marks_synthesis_with_missing_page_stale(
    vault_dir: Path, capsys: pytest.CaptureFixture[str], standalone_only: None
) -> None:
    """S16 companion: ``analecta index`` marks a synthesis whose page file
    is deleted with the same `` — STALE`` line shape, exit 0, no traceback.
    Today no marker appears (the missing page classifies fresh) — red."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura sin página", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis borrada", "Cuerpo.", [capture.id])
    synth = _sole_synthesis(vault_dir)
    Path(synth.file_path).unlink()

    code = run(["index"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 0
    expected = f"- [{synth.title}]({_resource_of(synth)}) — STALE"
    lines = out.splitlines()
    assert expected in lines
    synth_section = out.split("# Synthesis", 1)[1]
    assert expected in synth_section.splitlines()
    assert "Traceback" not in out


# ---------------------------------------------------------------------------
# T5 S20: ``index --json`` synthesis items carry a ``stale`` boolean
# ---------------------------------------------------------------------------


def test_index_json_synthesis_items_carry_stale_field(
    vault_dir: Path, capsys: pytest.CaptureFixture[str], standalone_only: None
) -> None:
    """S20: ``index --json`` synthesis items carry a boolean ``stale`` field
    (true for the drifted synthesis, false for the fresh one); capture
    items are unchanged — no ``stale`` key. Today no item carries the
    field: red."""
    index = _open_index(vault_dir)
    try:
        fresh_src = _capture(vault_dir, index, "Captura fresca json", "Cuerpo A.")
        stale_src = _capture(vault_dir, index, "Captura rancia json", "Cuerpo B.")
    finally:
        index.close()
    assert fresh_src.id is not None
    assert stale_src.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis json fresca", "Cuerpo.", [fresh_src.id])
    _synthesize_via_cli(vault_dir, "Síntesis json rancia", "Cuerpo.", [stale_src.id])
    _edit_body(Path(stale_src.file_path), "Cuerpo B.", "Cuerpo B editado.")
    # Drain the setup's own output so only the invocation under test is
    # observed (the setup legitimately prints its creation lines).
    capsys.readouterr()

    code = run(["index", "--json"], vault_path=vault_dir)
    out = capsys.readouterr().out

    assert code == 0
    data = json.loads(out)
    synths = {d["path"]: d for d in data if d["type"] == "synthesis"}
    assert synths, f"no synthesis items in index --json: {out!r}"
    index = _open_index(vault_dir)
    try:
        recs = [r for r in index.list_entries() if r.source_type == "synthesis"]
    finally:
        index.close()
    stale_record = next(r for r in recs if r.title == "Síntesis json rancia")
    fresh_record = next(r for r in recs if r.title == "Síntesis json fresca")
    for item in synths.values():
        assert "stale" in item, f"synthesis item lacks 'stale': {item!r}"
        assert isinstance(item["stale"], bool)
    assert synths[_resource_of(stale_record)]["stale"] is True
    assert synths[_resource_of(fresh_record)]["stale"] is False
    # Captures and other items are unchanged: no ``stale`` key.
    for item in data:
        if item["type"] != "synthesis":
            assert "stale" not in item


# ---------------------------------------------------------------------------
# T5 S22: creation-path missing-args wording mentions --update
# ---------------------------------------------------------------------------


def test_creation_missing_args_message_names_update_alternative(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S22 (R2-readability-4): the creation-path missing-args message says
    ``required unless --stale or --update`` — the old ``required unless
    --stale`` text understated the contract (``--update`` is a third way
    to invoke ``synthesize``). No test pinned the old wording positively;
    this pins the new one and is red at HEAD."""
    code = run(["synthesize"], vault_path=vault_dir)
    captured = capsys.readouterr()

    assert code == 2
    assert "required unless --stale or --update" in captured.err
    assert captured.out == ""


def test_stale_report_invalid_utf8_page_is_stale_no_traceback(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S16 unreadable variant: a synthesis page holding bytes that are not
    valid UTF-8 (hand-edit/sync artifact) is reported stale — its own page
    resource named — with no traceback, on both surfaces. read_text raises
    UnicodeDecodeError, which is a ValueError, not an OSError: the OSError
    guards alone do not cover it."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura binaria", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis binaria", "Cuerpo.", [capture.id])
    synth = _sole_synthesis(vault_dir)
    Path(synth.file_path).write_bytes(b"---\ntitle: 'S'\n---\n\n\xff\xfe not utf-8\n")
    for argv in (["synthesize", "--stale"], ["index"]):
        code = run(argv, vault_path=vault_dir)
        captured = capsys.readouterr()
        assert code == 1 if "--stale" in argv else code == 0
        assert "Traceback" not in captured.out + captured.err
        if "--stale" in argv:
            assert any(_resource_of(synth) in ln for ln in _stale_lines(captured.out))
        else:
            assert f"- [{synth.title}]({_resource_of(synth)}) — STALE" in captured.out
