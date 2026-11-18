"""T9: the vault-root ``AGENTS.md`` manifest (D4).

The manifest documents the vault's own grammar for external agents:
wikilinks/hashtags/``linked:`` semantics, the extra-OKF frontmatter fields,
the description-vs-summary distinction, the flags contract (bots read,
never write), the single-writer rule, and the D9 freshness rule. It is
generated — perpetual voice, never hand-maintained — and rewritten on every
sidecar startup / CLI vault run.
"""

from pathlib import Path

import pytest

from analecta.manifest import MANIFEST_FILE_NAME, write_vault_manifest


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "pages").mkdir()
    return tmp_path


def test_manifest_is_written_at_vault_root(vault: Path) -> None:
    write_vault_manifest(vault)
    path = vault / MANIFEST_FILE_NAME
    assert path.name == "AGENTS.md"
    assert path.exists()


def test_manifest_content_covers_the_contract(vault: Path) -> None:
    write_vault_manifest(vault)
    text = (vault / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
    for needle in (
        "[[Wikilink]]",
        "casefold",
        "linked:",
        "read_state",
        "summary_basis",
        "draft|stable|deprecated",
        "bookmark",
        "gem",
        "archive",
        "flock",
        "type: reference",
        "type: synthesis",
        "summary",
        "description",
    ):
        assert needle in text, f"manifest must document {needle!r}"


def test_manifest_flags_are_read_only_for_bots(vault: Path) -> None:
    write_vault_manifest(vault)
    text = (vault / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
    assert "never write" in text.lower() or "must not write" in text.lower()


def test_manifest_is_idempotent_and_regenerated(vault: Path) -> None:
    stale = vault / MANIFEST_FILE_NAME
    stale.write_text("stale content that must be replaced\n", encoding="utf-8")
    write_vault_manifest(vault)
    text = stale.read_text(encoding="utf-8")
    assert "stale content" not in text
    assert write_vault_manifest(vault) == write_vault_manifest(vault)
    assert stale.read_text(encoding="utf-8") == text
