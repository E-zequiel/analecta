"""Logseq template page manager — M6 PKM layer."""

from pathlib import Path

from analecta.markdown.frontmatter import build_template_block


def write_template_page(vault_path: Path, source_type: str) -> Path:
    """Write a Logseq template page for *source_type* to the vault.

    The file is written to ``{vault_path}/pages/template-{source_type}.md``
    and can be inserted via Logseq's template picker.

    Args:
        vault_path: Root vault directory.
        source_type: One of ``"article"``, ``"youtube"``, ``"substack"``,
            ``"x"``.

    Returns:
        Path to the written template file.
    """
    pages_dir = vault_path / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    dest = pages_dir / f"template-{source_type}.md"
    dest.write_text(build_template_block(source_type), encoding="utf-8")
    return dest


def list_template_pages(vault_path: Path) -> list[Path]:
    """Return all template pages present in the vault.

    Args:
        vault_path: Root vault directory.

    Returns:
        Sorted list of ``Path`` objects for ``template-*.md`` files in
        ``{vault_path}/pages/``. Empty if none exist.
    """
    pages_dir = vault_path / "pages"
    if not pages_dir.exists():
        return []
    return sorted(pages_dir.glob("template-*.md"))
