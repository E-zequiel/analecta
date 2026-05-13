"""HTML-to-Markdown converter — M4 pipeline."""

import re
from typing import Any

import markdownify as markdownify_lib
from bs4 import Tag

from analecta.extraction.core import ExtractedContent
from analecta.markdown.frontmatter import build_frontmatter

_STRIP_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


class _Converter(markdownify_lib.MarkdownConverter):
    """markdownify subclass that fixes double-fencing of <pre><code> blocks.

    markdownify processes both the <pre> wrapper and the inner <code> tag,
    producing six backticks instead of three. This override handles the pair
    as a unit, delegating inner text extraction directly to BeautifulSoup.
    """

    def convert_pre(self, el: Tag, text: str, convert_as_inline: bool) -> str:  # type: ignore[override]
        code = el.find("code")
        if isinstance(code, Tag):
            classes = code.get("class") or []
            lang = " ".join(str(c) for c in classes).replace("language-", "").strip()
            return f"\n\n```{lang}\n{code.get_text()}\n```\n\n"
        return f"\n\n```\n{text.strip()}\n```\n\n"


def _md(**kwargs: Any) -> _Converter:
    return _Converter(heading_style="ATX", bullets="-", **kwargs)


class MarkdownConverter:
    """Converts ``ExtractedContent`` to a complete Markdown document.

    The output is a YAML-frontmatter block followed by the article body
    converted from HTML using ``markdownify``.
    """

    def convert(self, content: ExtractedContent, created_at: str) -> str:
        """Produce a full Markdown document from *content*.

        Args:
            content: Extracted content from M2/M3.
            created_at: ISO 8601 timestamp string for the ``created_at`` field.

        Returns:
            Complete Markdown string: YAML frontmatter + converted body.
        """
        frontmatter = build_frontmatter(content, created_at)
        body = self._html_to_md(content.html)
        return frontmatter + "\n" + body

    def _html_to_md(self, html: str) -> str:
        """Convert *html* to Markdown.

        Args:
            html: HTML string (trafilatura or readability output).

        Returns:
            Markdown string with ATX headings and ``-`` list bullets.
        """
        clean = _STRIP_RE.sub("", html)
        return _md().convert(clean)
