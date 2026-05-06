"""HTML-to-Markdown converter — M4 pipeline."""

import re

import markdownify

from analecta.extraction.core import ExtractedContent
from analecta.markdown.frontmatter import build_frontmatter

_STRIP_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


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
        return markdownify.markdownify(clean, heading_style="ATX", bullets="-")
