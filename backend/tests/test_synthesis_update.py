"""T3: CLI ``synthesize --update`` in-place regeneration (S5/S6 of okf-cli).

S5 — ``analecta synthesize --update <ref>`` replaces an existing synthesis
in place: same file path, same slug/URL, same DB row id. It refreshes the
body, ``sources[]``, ``generated.at`` and ``sources_basis`` — the pairing
rewrite of ``sources_basis`` together with ``sources[]`` is mandatory (T1
advisor note) — and afterwards the entry is not stale. ``<ref>`` resolves
like other CLI entry refs (numeric id or exact URL).

S6 — ``--update`` error paths exit non-zero and leave the vault unchanged:
unknown ref; ref that is not a synthesis (a capture); no ``--source``.

Pinned design decisions (Agent A, for Agent B to implement):

- ``--update`` takes the synthesis ref plus regeneration arguments:
  ``--body`` (required; refreshed), ``--source`` (required, repeatable —
  the new citation set), ``--by`` (optional; when given it refreshes
  ``generated.by``). The title is NOT refreshed — the slug (hence the URL
  and the file path) derives from it and S5 pins those unchanged;
  ``--title`` together with ``--update`` is deliberately left untested
  (owner call pending, out of T3 scope).
- ``--stale`` wins over ``--update`` (L6/L7): ``--update`` joins the
  unified conflict-rejection list, so ``synthesize --stale --update <ref>``
  exits 2 naming both flags (S10 mechanism), never runs the report.
- ``--update --json`` is rejected with the same unified message shape
  (exit 2, both flags named): ``--json`` stays report-only, consistent
  with the committed ``--stale --json`` rejection.
- ``--update`` handling precedes the generic missing-args check (L6
  constraint): ``synthesize --update <ref>`` without ``--source`` hits
  the ``--update``-specific missing-args error (exit 2, message names
  ``--update`` and ``--source``), never the generic ``required unless
  --stale`` message.
- Exit codes follow the existing convention: runtime failures (unknown
  ref, not-a-synthesis ref) exit 1; usage errors (conflicts, missing
  args) exit 2.

Demonstrated-red note: at HEAD, ``--update`` is not a known ``synthesize``
argument, so argparse rejects every invocation below with ``SystemExit``
(exit 2) before any assertion runs — that rejection IS the observed red
for S5/S6, the same shape test_synthesis_freshness.py documents for S2.

Scenarios are built through the real CLI against an injected vault
(standalone mode; ``synthesize`` never touches the attached-mode
discovery). No network calls, no writes under backend/src/.
"""

import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from analecta.cli import run
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Empty vault root (pages/ and assets/ created)."""
    VaultManager(tmp_path / "vault").ensure_dirs()
    return tmp_path / "vault"


def _open_index(vault_dir: Path) -> VaultIndex:
    return VaultIndex(vault_dir / "analecta.db")


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


def _frontmatter(path: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter dict of a page (mirrors FM_BLOCK)."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    data = yaml.safe_load(text[4 : text.index("\n---\n")])
    assert isinstance(data, dict)
    return data


def _edit_frontmatter(page: Path, updates: dict[str, Any]) -> None:
    """Hand-edit a page's YAML frontmatter in place (out-of-app edit)."""
    text = page.read_text(encoding="utf-8")
    data = _frontmatter(page)
    data.update(updates)
    body = yaml.dump(data, allow_unicode=True, sort_keys=False)
    end = text.index("\n---\n") + len("\n---\n")
    page.write_text(f"---\n{body}---\n{text[end:]}", encoding="utf-8")


def _post_frontmatter_body_bytes(path: Path) -> bytes:
    """Post-frontmatter body bytes exactly as stored on disk (S1 identity)."""
    data = path.read_bytes()
    assert data.startswith(b"---\n")
    end = data.index(b"\n---\n") + len(b"\n---\n")
    return data[end:]


def _basis(path: Path) -> str:
    """sha256[:16] UTF-8 hexdigest of the on-disk post-frontmatter body."""
    return hashlib.sha256(_post_frontmatter_body_bytes(path)).hexdigest()[:16]


def _vault_snapshot(vault_dir: Path) -> tuple[list[tuple[Any, ...]], dict[str, bytes]]:
    """Full vault state: every DB row and every page file's bytes."""
    index = _open_index(vault_dir)
    try:
        rows = sorted(
            (r.id, r.url, r.file_path, r.source_type, r.title)
            for r in index.list_entries()
        )
    finally:
        index.close()
    pages = {p.name: p.read_bytes() for p in sorted((vault_dir / "pages").glob("*.md"))}
    return rows, pages


def _assert_vault_unchanged(
    vault_dir: Path, before: tuple[list[tuple[Any, ...]], dict[str, bytes]]
) -> None:
    assert _vault_snapshot(vault_dir) == before


def _drain(capsys: pytest.CaptureFixture[str]) -> None:
    """Discard setup output so only the invocation under test is observed."""
    capsys.readouterr()


# ---------------------------------------------------------------------------
# S5: in-place regeneration
# ---------------------------------------------------------------------------


def test_update_stale_synthesis_regenerates_in_place(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S5: updating a stale synthesis keeps path/URL/row id, refreshes the
    rest, and the entry is fresh again.

    Preconditions: a synthesis citing one capture, whose body is edited
    afterwards (drift → stale). The update re-cites the same capture, so
    ``sources_basis`` must be rewritten to the capture's *current* body
    hash — a stale-check pass afterwards proves the rewrite happened.
    """
    index = _open_index(vault_dir)
    try:
        capture = _capture(
            vault_dir, index, "Captura Original", "Cuerpo original íntegro."
        )
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis a actualizar", "Cuerpo viejo de la síntesis.", [capture.id]
    )
    _edit_body(
        Path(capture.file_path),
        "Cuerpo original íntegro.",
        "Cuerpo editado tras la síntesis.",
    )

    # Precondition: the synthesis is stale before the update.
    assert run(["synthesize", "--stale"], vault_path=vault_dir) == 1
    _drain(capsys)

    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None
    old_at = _frontmatter(Path(synth.file_path))["generated"]["at"]

    code = run(
        [
            "synthesize",
            "--update",
            str(synth.id),
            "--body",
            "Cuerpo nuevo de la síntesis.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    out = capsys.readouterr()
    assert code == 0, out

    synth2 = _sole_synthesis(vault_dir)
    # In-place guarantees: same DB row id, same file path, same slug/URL.
    assert synth2.id == synth.id
    assert synth2.file_path == synth.file_path
    assert synth2.url == synth.url
    # The title is not refreshed (the slug must keep matching the URL).
    assert synth2.title == synth.title

    page = Path(synth2.file_path)
    data = _frontmatter(page)
    capture_page = Path(capture.file_path)
    assert data["sources"] == [
        {"resource": _resource_of(capture), "title": capture.title}
    ]
    # sources_basis is rewritten together with sources[] to the capture's
    # current (edited) body hash — not the hash recorded at creation time.
    assert data["sources_basis"] == [
        {"resource": _resource_of(capture), "basis": _basis(capture_page)}
    ]
    # --by refreshes generated.by; generated.at is bumped.
    assert data["generated"]["by"] == "bot/y"
    assert datetime.fromisoformat(
        str(data["generated"]["at"])
    ) > datetime.fromisoformat(str(old_at))
    # The body is refreshed.
    text = page.read_text(encoding="utf-8")
    assert "Cuerpo nuevo de la síntesis." in text
    assert "Cuerpo viejo de la síntesis." not in text

    # Afterwards the entry is not stale: the report confirms freshness.
    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    fresh_out = capsys.readouterr().out
    assert code == 0
    assert "Síntesis a actualizar" in fresh_out
    assert not any(
        "stale" in ln.lower() for ln in fresh_out.splitlines() if "Síntesis" in ln
    )


def test_update_by_url_replaces_sources_and_basis_in_source_order(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S5: --update accepts a URL ref and replaces the citation set.

    The synthesis is updated by its ``analecta://synthesis/<slug>`` URL and
    re-cited with a *changed* source set (C then B, replacing A and B):
    both ``sources[]`` and ``sources_basis`` must be replaced wholesale, in
    ``--source`` order — the old citation must be gone and the pairing
    rewritten (T1 advisor note).
    """
    index = _open_index(vault_dir)
    try:
        a = _capture(vault_dir, index, "Captura Alpha", "Cuerpo de Alpha.")
        b = _capture(vault_dir, index, "Captura Beta", "Cuerpo de Beta, con ñ.")
        c = _capture(vault_dir, index, "Captura Gamma", "Cuerpo de Gamma.")
    finally:
        index.close()
    assert a.id is not None
    assert b.id is not None
    assert c.id is not None
    _synthesize_via_cli(vault_dir, "Síntesis recitada", "Cuerpo viejo.", [a.id, b.id])
    _drain(capsys)

    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None

    code = run(
        [
            "synthesize",
            "--update",
            synth.url,
            "--body",
            "Cuerpo nuevo.",
            "--source",
            str(c.id),
            "--source",
            str(b.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    assert code == 0

    synth2 = _sole_synthesis(vault_dir)
    assert synth2.id == synth.id
    assert synth2.file_path == synth.file_path
    assert synth2.url == synth.url

    data = _frontmatter(Path(synth2.file_path))
    assert data["sources"] == [
        {"resource": _resource_of(c), "title": c.title},
        {"resource": _resource_of(b), "title": b.title},
    ]
    assert data["sources_basis"] == [
        {"resource": _resource_of(c), "basis": _basis(Path(c.file_path))},
        {"resource": _resource_of(b), "basis": _basis(Path(b.file_path))},
    ]

    # The replaced citation set leaves the synthesis fresh.
    code = run(["synthesize", "--stale"], vault_path=vault_dir)
    assert code == 0
    _drain(capsys)


# ---------------------------------------------------------------------------
# S6: error paths — non-zero exit, vault unchanged
# ---------------------------------------------------------------------------


def test_update_unknown_ref_exits_one_and_leaves_vault_unchanged(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S6: an unknown ``--update`` ref fails naming the ref, touching
    nothing."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura Sana", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis intacta", "Cuerpo de la síntesis.", [capture.id]
    )
    _drain(capsys)
    before = _vault_snapshot(vault_dir)

    code = run(
        [
            "synthesize",
            "--update",
            "424242",
            "--body",
            "Cuerpo que no debe escribirse.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    captured = capsys.readouterr()

    assert code == 1
    # The error names the unresolved ref and goes to stderr.
    assert "424242" in captured.err
    assert captured.out == ""
    _assert_vault_unchanged(vault_dir, before)


def test_update_capture_ref_is_rejected_and_leaves_vault_unchanged(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S6: a ref that resolves to a capture (not a synthesis) fails naming
    the ref, touching nothing. Exercised with the URL ref form."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura No Síntesis", "Cuerpo.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis testigo", "Cuerpo de la síntesis.", [capture.id]
    )
    _drain(capsys)
    before = _vault_snapshot(vault_dir)

    code = run(
        [
            "synthesize",
            "--update",
            capture.url,
            "--body",
            "Cuerpo que no debe escribirse.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    captured = capsys.readouterr()

    assert code == 1
    # The error names the rejected ref (and that it is not a synthesis).
    assert capture.url in captured.err
    assert "synthesis" in captured.err
    assert captured.out == ""
    _assert_vault_unchanged(vault_dir, before)


def test_update_without_source_is_update_specific_missing_args(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S6 + L6 constraint: ``--update <ref>`` without ``--source`` is an
    --update-specific missing-args error, not the generic creation-path
    message — so ``--update`` must be resolved before the generic
    missing-args check."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura Fuente", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis sin recita", "Cuerpo de la síntesis.", [capture.id]
    )
    _drain(capsys)
    before = _vault_snapshot(vault_dir)
    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None

    code = run(["synthesize", "--update", str(synth.id)], vault_path=vault_dir)
    captured = capsys.readouterr()

    # Usage-style error (missing-args convention), not a runtime failure.
    assert code == 2
    # --update-specific: the message names --update and --source, and is
    # NOT the generic creation-path "required unless --stale" wording.
    assert "--update" in captured.err
    assert "--source" in captured.err
    assert "required unless --stale" not in captured.err
    assert captured.out == ""
    _assert_vault_unchanged(vault_dir, before)


# ---------------------------------------------------------------------------
# Precedence / interplay (L6 constraint + L7 advisories)
# ---------------------------------------------------------------------------


def test_stale_wins_over_update_exits_two_naming_both(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--stale`` beats ``--update``: the invocation joins the unified
    conflict rejection (S10 mechanism) — exit 2 naming both flags, no
    report printed even though a stale synthesis exists, vault untouched."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(
            vault_dir, index, "Captura Precedencia", "Cuerpo original íntegro."
        )
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis estancada", "Cuerpo de la síntesis.", [capture.id]
    )
    _edit_body(
        Path(capture.file_path),
        "Cuerpo original íntegro.",
        "Cuerpo editado tras la síntesis.",
    )
    _drain(capsys)
    before = _vault_snapshot(vault_dir)
    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None

    code = run(
        [
            "synthesize",
            "--stale",
            "--update",
            str(synth.id),
            "--body",
            "Cuerpo que no debe escribirse.",
            "--source",
            str(capture.id),
        ],
        vault_path=vault_dir,
    )
    captured = capsys.readouterr()

    assert code == 2
    # Both conflicting flags are named on stderr.
    assert "--stale" in captured.err
    assert "--update" in captured.err
    # The staleness report never runs, despite the stale synthesis.
    assert captured.out == ""
    _assert_vault_unchanged(vault_dir, before)


def test_update_with_title_is_rejected_naming_both(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner-ratified T3 contract item: ``--update --title`` is an explicit
    conflict, not a silent ignore.

    The title of an in-place update is never refreshed (the slug — hence
    the URL and the file path — derives from it and S5 pins those
    unchanged), so passing ``--title`` alongside ``--update`` must be
    rejected as a conflicting flag: exit 2 (usage-error class, per the
    file's pinned table), stderr naming BOTH ``--update`` and ``--title``,
    no report, no creation, no update — the vault snapshot is unchanged
    (same row id, same title, same body, same ``generated.at``)."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura Título", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis titulada", "Cuerpo original de la síntesis.", [capture.id]
    )
    _drain(capsys)
    before = _vault_snapshot(vault_dir)
    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None

    code = run(
        [
            "synthesize",
            "--update",
            str(synth.id),
            "--title",
            "Nuevo título",
            "--body",
            "Cuerpo que no debe escribirse.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    captured = capsys.readouterr()

    assert code == 2
    # Both conflicting flags are named on stderr.
    assert "--update" in captured.err
    assert "--title" in captured.err
    # No report, no creation, no update: the vault is byte-identical
    # (snapshot covers every DB row and every page file's bytes).
    assert captured.out == ""
    _assert_vault_unchanged(vault_dir, before)
    # Belt-and-braces on the snapshot's own fields: same row id, same
    # title, same body, same generated.at.
    synth2 = _sole_synthesis(vault_dir)
    assert synth2.id == synth.id
    assert synth2.title == synth.title
    assert "Cuerpo que no debe escribirse." not in Path(synth2.file_path).read_text(
        encoding="utf-8"
    )
    assert (
        _frontmatter(Path(synth2.file_path))["generated"]["at"]
        == _frontmatter(Path(synth.file_path))["generated"]["at"]
    )


def test_update_with_json_is_rejected_naming_both(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pinned decision: ``--update --json`` is rejected (exit 2, both flags
    named on stderr), consistent with ``--json`` being report-only for
    ``--stale`` (S10). Nothing is regenerated and the vault is untouched."""
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura Json", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis json", "Cuerpo de la síntesis.", [capture.id]
    )
    _drain(capsys)
    before = _vault_snapshot(vault_dir)
    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None

    code = run(
        [
            "synthesize",
            "--update",
            str(synth.id),
            "--body",
            "Cuerpo que no debe escribirse.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
            "--json",
        ],
        vault_path=vault_dir,
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "--update" in captured.err
    assert "--json" in captured.err
    assert captured.out == ""
    _assert_vault_unchanged(vault_dir, before)


# ---------------------------------------------------------------------------
# T3b: S11 (frontmatter preservation on hand-edited type), S12 (summary
# clearing), S13 (updated_at bump), S14 (green pins: extras + created_at)
# ---------------------------------------------------------------------------


def test_update_preserves_extra_keys_when_type_was_hand_edited(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S11 (T3 advisor A1): a synthesis page whose frontmatter is parseable
    YAML but whose ``type`` was hand-edited away from ``synthesis`` keeps
    every non-canonical extra key across ``--update`` — the DB row already
    proved the entry is a synthesis, so a parseable dict is preserved; only
    unparseable frontmatter rebuilds from DB defaults.

    Canonical keys come from the DB row / new values: ``title``/``url``/
    ``source_type``/``created_at`` (never refreshed), ``tags``/``read_state``
    (DB row values), ``generated.at`` (bumped) and the ``sources[]``/
    ``sources_basis`` pairing rewritten to the cited capture's current
    body hash.
    """
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura Manos", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis editada", "Cuerpo de la síntesis.", [capture.id]
    )
    _drain(capsys)
    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None
    page = Path(synth.file_path)
    original_created = _frontmatter(page)["created_at"]
    old_generated_at = _frontmatter(page)["generated"]["at"]
    # Hand-edit: type drifts away from synthesis, extras are added.
    _edit_frontmatter(page, {"type": "note", "status": "stable", "custom_key": "x"})

    code = run(
        [
            "synthesize",
            "--update",
            str(synth.id),
            "--body",
            "Cuerpo nuevo de la síntesis.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    out = capsys.readouterr()
    assert code == 0, out

    synth2 = _sole_synthesis(vault_dir)
    assert synth2.id == synth.id
    assert synth2.file_path == synth.file_path
    data = _frontmatter(page)
    # The extra keys survive the rewrite (today they are discarded).
    assert data["status"] == "stable"
    assert data["custom_key"] == "x"
    # Canonical keys come from the DB row / new values.
    assert data["title"] == synth.title
    assert data["url"] == synth.url
    assert data["source_type"] == "synthesis"
    assert data["created_at"] == original_created
    assert data["tags"] == []
    assert data["read_state"] == "unread"
    assert data["sources"] == [
        {"resource": _resource_of(capture), "title": capture.title}
    ]
    assert data["sources_basis"] == [
        {
            "resource": _resource_of(capture),
            "basis": _basis(Path(capture.file_path)),
        }
    ]
    new_at = datetime.fromisoformat(str(data["generated"]["at"]))
    assert new_at > datetime.fromisoformat(str(old_generated_at))
    # The body is refreshed.
    text = page.read_text(encoding="utf-8")
    assert "Cuerpo nuevo de la síntesis." in text


def test_update_clears_recorded_summary_frontmatter_and_db(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S12 (T3 advisor A5, ratified): a synthesis WITH a recorded summary
    (frontmatter ``summary``/``summary_basis`` + mirrored DB columns, set
    the way the app does: frontmatter via the ``summary`` CLI surface,
    DB columns via ``VaultIndex.update_summary``) has both fully cleared by
    ``--update`` — a new body never lingers with a stale summary.
    """
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura Resumen", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis resumida", "Cuerpo de la síntesis.", [capture.id]
    )
    _drain(capsys)
    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None
    page = Path(synth.file_path)
    # Record a summary the way the app does: frontmatter via the CLI
    # ``summary`` command, DB mirror via ``update_summary`` (the PATCH
    # route's mechanism).
    assert (
        run(
            [
                "summary",
                str(synth.id),
                "--text",
                "Resumen previo de la síntesis.",
                "--by",
                "bot/x",
            ],
            vault_path=vault_dir,
        )
        == 0
    )
    basis = _basis(page)
    index = _open_index(vault_dir)
    try:
        index.update_summary(synth.id, "Resumen previo de la síntesis.", basis)
    finally:
        index.close()
    _drain(capsys)
    # Precondition: the summary is recorded frontmatter-side and DB-side.
    assert _frontmatter(page)["summary"] == "Resumen previo de la síntesis."
    assert _frontmatter(page)["summary_basis"] == basis
    pre = _sole_synthesis(vault_dir)
    assert pre.summary == "Resumen previo de la síntesis."
    assert pre.summary_basis == basis

    code = run(
        [
            "synthesize",
            "--update",
            str(synth.id),
            "--body",
            "Cuerpo nuevo de la síntesis.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    out = capsys.readouterr()
    assert code == 0, out

    # Frontmatter: the summary keys are gone entirely (today they persist).
    data = _frontmatter(page)
    assert "summary" not in data
    assert "summary_basis" not in data
    # DB: the mirrored columns are empty (today they persist stale).
    post = _sole_synthesis(vault_dir)
    assert post.summary is None
    assert post.summary_basis is None


def test_update_bumps_db_row_updated_at(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S13 (T3 advisor A3, ratified): ``--update`` bumps the DB row's
    ``updated_at`` to strictly later than before — the API "last-update"
    becomes truthful. Asserted via the index records (list_entries).
    """
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura Frescura", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis fresca", "Cuerpo de la síntesis.", [capture.id]
    )
    _drain(capsys)
    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None
    before = synth.updated_at
    # Guarantee the timestamps differ even at microsecond resolution.
    time.sleep(0.002)

    code = run(
        [
            "synthesize",
            "--update",
            str(synth.id),
            "--body",
            "Cuerpo nuevo de la síntesis.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    out = capsys.readouterr()
    assert code == 0, out

    after = _sole_synthesis(vault_dir)
    assert datetime.fromisoformat(after.updated_at) > datetime.fromisoformat(before)


def test_update_pins_extra_keys_and_created_at_when_type_canonical(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S14 (cheap pins, GREEN): with a canonical ``type: synthesis``, extra
    frontmatter keys (``status: stable``, ``custom_key: x``) survive
    ``--update`` AND the original ``created_at`` is unchanged — both
    invariants hold today and must keep holding after the S11 fix and the
    S12 clearing cannot regress them.
    """
    index = _open_index(vault_dir)
    try:
        capture = _capture(vault_dir, index, "Captura Pin", "Cuerpo íntegro.")
    finally:
        index.close()
    assert capture.id is not None
    _synthesize_via_cli(
        vault_dir, "Síntesis pineada", "Cuerpo de la síntesis.", [capture.id]
    )
    _drain(capsys)
    synth = _sole_synthesis(vault_dir)
    assert synth.id is not None
    page = Path(synth.file_path)
    original_created = _frontmatter(page)["created_at"]
    _edit_frontmatter(page, {"status": "stable", "custom_key": "x"})

    code = run(
        [
            "synthesize",
            "--update",
            str(synth.id),
            "--body",
            "Cuerpo nuevo de la síntesis.",
            "--source",
            str(capture.id),
            "--by",
            "bot/y",
        ],
        vault_path=vault_dir,
    )
    out = capsys.readouterr()
    assert code == 0, out

    synth2 = _sole_synthesis(vault_dir)
    assert synth2.id == synth.id
    data = _frontmatter(page)
    # Extra keys survive (today's behavior, pinned).
    assert data["status"] == "stable"
    assert data["custom_key"] == "x"
    # created_at is unchanged, frontmatter-side and DB-side.
    assert data["created_at"] == original_created
    assert synth2.created_at == synth.created_at
