import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


def _slugify(text: str, max_len: int = 60) -> str:
    """Convert arbitrary text to a filesystem-safe ASCII slug.

    Args:
        text: Input string.
        max_len: Maximum length of the returned slug.

    Returns:
        Lowercase hyphen-separated ASCII string, at most ``max_len`` chars.
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    text = text.strip("-")
    return text[:max_len].strip("-")


class VaultManager:
    """Manages the vault directory tree and Markdown file I/O.

    Args:
        vault_path: Root directory of the vault.
    """

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path
        self.pages_path = vault_path / "pages"
        self.assets_path = vault_path / "assets"

    def ensure_dirs(self) -> None:
        """Create pages/ and assets/ subdirectories if they don't exist."""
        self.pages_path.mkdir(parents=True, exist_ok=True)
        self.assets_path.mkdir(parents=True, exist_ok=True)

    def page_path(self, title: str, date: datetime | None = None) -> Path:
        """Return the canonical path for a vault page.

        Args:
            title: Article title, used to build the slug.
            date: Publication date; defaults to current UTC time.

        Returns:
            Absolute path under pages/ following ``YYYY-MM-DD-{slug}.md``.
        """
        if date is None:
            date = datetime.now(tz=timezone.utc)
        slug = _slugify(title) or "entry"
        filename = f"{date.strftime('%Y-%m-%d')}-{slug}.md"
        return self.pages_path / filename

    def asset_dir(self, slug: str) -> Path:
        """Return the asset subdirectory for a given entry slug.

        Args:
            slug: Entry slug (from the page filename).

        Returns:
            Path to ``{vault}/assets/{slug}/``.
        """
        return self.assets_path / slug

    def write_page(self, content: str, title: str, date: datetime | None = None) -> Path:
        """Write Markdown content to the vault and return its path.

        Args:
            content: Full Markdown string to write.
            title: Used to derive the filename slug.
            date: Publication date; defaults to current UTC time.

        Returns:
            Path of the written file.
        """
        self.ensure_dirs()
        path = self.page_path(title, date)
        path.write_text(content, encoding="utf-8")
        return path
