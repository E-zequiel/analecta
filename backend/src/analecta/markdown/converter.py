"""HTML-to-Markdown converter — M4 pipeline."""

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import markdownify as markdownify_lib
from bs4 import BeautifulSoup, Tag

from analecta.extraction.core import ExtractedContent
from analecta.markdown.frontmatter import build_frontmatter

_STRIP_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)

# Elements whose sole purpose is to display a visible language label next to a code
# block (e.g. <span class="language-name">js</span>). Not actual article content.
_LANG_LABEL_CLASSES = re.compile(r"\blanguage-name\b")

# Matches short identifiers used as language names in code blocks: "js", "python",
# "c++", "c#", "bash", etc. Used to detect bare <p>lang</p> label paragraphs.
_LANG_HINT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-#]{0,14}$")


def _lang_from_pre(pre: Tag) -> str:
    """Extract language from a ``<pre>`` element's class list.

    Handles:
    - ``class="language-python"``
    - ``class="brush: js notranslate"`` (CodeMirror / MDN style)

    Args:
        pre: The ``<pre>`` element.

    Returns:
        Language name string, or empty string if not found.
    """
    pre_classes = [str(c) for c in (pre.get("class") or [])]
    for i, c in enumerate(pre_classes):
        if c == "brush:" and i + 1 < len(pre_classes):
            candidate = pre_classes[i + 1]
            if candidate not in ("notranslate", "copy-to-clipboard-button"):
                return candidate
        if c.startswith("language-"):
            return c[9:]
    return ""


def _get_lang(code: Tag, pre: Tag) -> str:
    """Extract the programming language name from a ``<pre>``/``<code>`` pair.

    Args:
        code: The inner ``<code>`` element.
        pre: The outer ``<pre>`` element.

    Returns:
        Language name string, or empty string if not found.
    """
    for c in code.get("class") or []:
        s = str(c)
        if s.startswith("language-"):
            return s[9:]
    return _lang_from_pre(pre)


def _resolve_img_src(src: str) -> str:
    """Unwrap Next.js /_next/image/ proxy URLs to the underlying CDN URL."""
    if not src.startswith("/_next/image/"):
        return src
    qs = parse_qs(urlparse(src).query)
    urls = qs.get("url", [])
    return unquote(urls[0]) if urls else src


def _normalize_html(soup: BeautifulSoup) -> None:
    """Normalize structural quirks common in extractor HTML output.

    Handles patterns produced by trafilatura and similar extractors:
    - ``<pre>`` directly inside ``<p>``: was inline code — convert to ``<code>``
    - ``<pre><pre>…</pre></pre>``: double-wrapped block — unwrap outer shell
    - ``<p>lang</p><pre>…</pre>``: bare language-label paragraph preceding a code
      block — promote the label into a ``language-*`` class on ``<pre>`` and remove
      the paragraph, so ``_lang_from_pre`` can recover the annotation later
    - ``<graphic>`` (trafilatura TEI image element) → ``<img>`` so markdownify
      renders it as Markdown image syntax
    """
    for graphic in list(soup.find_all("graphic")):
        src = _resolve_img_src(str(graphic.get("src", "")))
        img = soup.new_tag("img", src=src, alt=graphic.get("alt", ""))
        graphic.replace_with(img)

    for p in soup.find_all("p"):
        for pre in list(p.find_all("pre", recursive=False)):
            pre.name = "code"

    for outer in list(soup.find_all("pre")):
        inner = outer.find("pre", recursive=False)
        if inner:
            outer.replace_with(inner)

    for pre in list(soup.find_all("pre")):
        prev = pre.find_previous_sibling()
        if not isinstance(prev, Tag) or prev.name != "p":
            continue
        label = prev.get_text(strip=True)
        if not _LANG_HINT_RE.match(label):
            continue
        existing = list(pre.get("class") or [])
        if not any(str(c).startswith("language-") for c in existing):
            pre["class"] = [*existing, f"language-{label}"]
        prev.decompose()


class _Converter(markdownify_lib.MarkdownConverter):
    """markdownify subclass that preserves code language annotations.

    Handles patterns missed by the base converter:
    - ``<code class="language-python">`` on the inner tag
    - ``<pre class="brush: js notranslate">`` (MDN / CodeMirror style)
    - ``<pre class="language-js">`` promoted by ``_normalize_html``
    """

    def convert_pre(self, el: Tag, text: str, **kwargs: Any) -> str:  # type: ignore[override]
        code = el.find("code")
        if isinstance(code, Tag):
            lang = _get_lang(code, el)
            return f"\n\n```{lang}\n{code.get_text()}\n```\n\n"
        return f"\n\n```{_lang_from_pre(el)}\n{text.strip()}\n```\n\n"


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
            html: HTML string (readability or trafilatura output).

        Returns:
            Markdown string with ATX headings and ``-`` list bullets.
        """
        clean = _STRIP_RE.sub("", html)
        soup = BeautifulSoup(clean, "html.parser")
        _normalize_html(soup)
        for el in soup.find_all(class_=_LANG_LABEL_CLASSES):
            el.decompose()
        return _md().convert(str(soup))
