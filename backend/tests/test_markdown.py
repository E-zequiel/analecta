from typing import Any

import pytest
import yaml

from analecta.extraction.core import ExtractedContent
from analecta.markdown.converter import MarkdownConverter
from analecta.markdown.frontmatter import build_frontmatter, update_linked
from analecta.markdown.hashtags import title_to_hashtag_key

_CREATED_AT = "2024-01-15T10:00:00"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content(**kwargs) -> ExtractedContent:
    defaults: dict[str, Any] = {
        "title": "Test Article",
        "html": "<p>Hello world</p>",
        "url": "https://example.com/article",
        "source_type": "article",
    }
    defaults.update(kwargs)
    return ExtractedContent(**defaults)


# ---------------------------------------------------------------------------
# build_frontmatter
# ---------------------------------------------------------------------------


def test_frontmatter_has_delimiters():
    fm = build_frontmatter(_content(), _CREATED_AT)
    assert fm.startswith("---\n")
    assert fm.rstrip("\n").endswith("---")


def test_frontmatter_required_fields():
    fm = build_frontmatter(_content(), _CREATED_AT)
    assert "title:" in fm
    assert "url:" in fm
    assert "source_type:" in fm
    assert "created_at:" in fm
    assert "tags: []" in fm
    assert "status: unread" in fm


def test_frontmatter_is_valid_yaml():
    fm = build_frontmatter(_content(), _CREATED_AT)
    inner = fm.split("---\n", 2)[1]
    data = yaml.safe_load(inner)
    assert data["title"] == "Test Article"
    assert data["url"] == "https://example.com/article"
    assert data["source_type"] == "article"
    assert data["tags"] == []
    assert data["status"] == "unread"


def test_frontmatter_escapes_title_with_colon():
    content = _content(title='Breaking: "Quotes" and colons')
    fm = build_frontmatter(content, _CREATED_AT)
    inner = fm.split("---\n", 2)[1]
    data = yaml.safe_load(inner)
    assert data["title"] == 'Breaking: "Quotes" and colons'


def test_frontmatter_source_type_youtube():
    content = _content(source_type="youtube")
    fm = build_frontmatter(content, _CREATED_AT)
    assert "source_type: youtube" in fm


def test_frontmatter_includes_metadata_fields():
    content = _content(
        metadata={
            "author": "Alice",
            "description": "A summary",
            "published": "2024-06-01",
        }
    )
    fm = build_frontmatter(content, _CREATED_AT)
    inner = fm.split("---\n", 2)[1]
    data = yaml.safe_load(inner)
    assert data["author"] == "Alice"
    assert data["description"] == "A summary"
    assert data["published"] == "2024-06-01"


def test_frontmatter_omits_absent_metadata_fields():
    content = _content(metadata={"extractor": "readability"})
    fm = build_frontmatter(content, _CREATED_AT)
    assert "author:" not in fm
    assert "description:" not in fm
    assert "published:" not in fm


def test_frontmatter_includes_low_confidence_true():
    content = _content(metadata={"low_confidence": True})
    fm = build_frontmatter(content, _CREATED_AT)
    inner = fm.split("---\n", 2)[1]
    data = yaml.safe_load(inner)
    assert data["low_confidence"] is True


def test_frontmatter_includes_low_confidence_false():
    content = _content(metadata={"low_confidence": False})
    fm = build_frontmatter(content, _CREATED_AT)
    inner = fm.split("---\n", 2)[1]
    data = yaml.safe_load(inner)
    assert data["low_confidence"] is False


def test_frontmatter_omits_low_confidence_when_absent():
    content = _content(metadata={"extractor": "readability"})
    fm = build_frontmatter(content, _CREATED_AT)
    assert "low_confidence:" not in fm


# ---------------------------------------------------------------------------
# MarkdownConverter.convert
# ---------------------------------------------------------------------------


def test_convert_starts_with_frontmatter():
    md = MarkdownConverter().convert(_content(), _CREATED_AT)
    assert md.startswith("---\n")


def test_convert_contains_body():
    md = MarkdownConverter().convert(_content(html="<p>Hello world</p>"), _CREATED_AT)
    assert "Hello world" in md


def test_convert_uses_atx_headings():
    content = _content(html="<h1>Top</h1><h2>Sub</h2>")
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "# Top" in md
    assert "## Sub" in md


def test_convert_uses_dash_bullets():
    content = _content(html="<ul><li>Alpha</li><li>Beta</li></ul>")
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "- Alpha" in md
    assert "- Beta" in md


def test_convert_strips_script_tags():
    content = _content(html="<p>Text</p><script>alert(1)</script>")
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "alert" not in md


def test_convert_preserves_img_src():
    content = _content(html='<img src="../assets/slug/abc.jpg" alt="photo">')
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "../assets/slug/abc.jpg" in md


def test_convert_img_inside_heading_renders_as_image_syntax():
    # Images inside headings must produce ![alt](src), not just the alt text.
    content = _content(
        html=(
            '<h2><img src="https://cdn.example.com/img.png"'
            ' alt="cover-photo"/>Title</h2>'
        )
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "![cover-photo](https://cdn.example.com/img.png)" in md


def test_convert_collapses_hard_break_inside_link_text():
    # A <br> inside an <a> that isn't nested under a table/heading (e.g. after
    # _rescue_linked_tables flattens a <td> into a bare <p>) produces a literal
    # hard break ("  \n") in markdownify's output. Left uncollapsed, a second
    # line starting with "#" gets parsed as an ATX heading and breaks the link.
    content = _content(
        html=(
            '<p><a href="https://drafts.csswg.org/css-cascade-5/#css-inheritance">'
            "CSS Cascading and Inheritance Level 5<br># css-inheritance</a></p>"
        )
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert (
        "[CSS Cascading and Inheritance Level 5 # css-inheritance]"
        "(https://drafts.csswg.org/css-cascade-5/#css-inheritance)" in md
    )
    assert "\n# css-inheritance" not in md


def test_convert_resolves_nextjs_image_proxy():
    encoded = "https%3A%2F%2Fcdn.example.com%2Fphoto.jpg"
    content = _content(
        html=f'<img src="/_next/image?url={encoded}&w=1080&q=75" alt="photo">'
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "https://cdn.example.com/photo.jpg" in md
    assert "/_next/image" not in md


def test_convert_code_lang_attribute_on_code_element():
    # Chakra UI's <Code> component (e.g. socket.dev's blog) has no
    # language-* class at all — just a plain lang="" attribute on <code>.
    content = _content(
        html='<pre class="css-1gw6m10"><code lang="json" class="chakra-code">'
        '{"a": 1}</code></pre>'
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "```json\n" in md


def test_convert_code_lang_attribute_on_bare_pre():
    # Fallback path (convert_pre when there's no inner <code>): lang attribute
    # lives directly on <pre>.
    content = _content(html='<pre lang="python">print(1)</pre>')
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "```python\n" in md


def test_convert_language_class_takes_priority_over_lang_attribute():
    content = _content(
        html='<pre><code class="language-python" lang="javascript">x = 1</code></pre>'
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "```python\n" in md


def test_convert_pandoc_sourcecode_class_on_pre_and_code():
    # Pandoc-generated static sites (arthurrump.com and similar) mark up code
    # blocks as <pre class="sourceCode html"><code class="sourceCode html">
    # — no "language-*" prefix, just a bare sibling class next to "sourceCode".
    content = _content(
        html='<pre class="sourceCode html"><code class="sourceCode html">'
        "&lt;p&gt;hi&lt;/p&gt;</code></pre>"
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "```html\n" in md


def test_convert_pandoc_sourcecode_with_line_numbers():
    # Pandoc adds "numberSource"/"numberLines" modifier classes when line
    # numbering is enabled — must not be mistaken for the language itself.
    content = _content(
        html='<pre class="numberSource sourceCode python numberLines">'
        '<code class="sourceCode python">print(1)</code></pre>'
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "```python\n" in md


def test_convert_bare_sourcecode_class_has_no_language():
    # Pandoc fences with an unspecified/unrecognized language get just
    # class="sourceCode" with no language sibling — must not crash and must
    # not fabricate a language.
    content = _content(
        html='<pre class="sourceCode"><code class="sourceCode">plain text</code></pre>'
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "```\nplain text\n```" in md


def test_convert_strips_backtick_run_from_lang_attribute_on_code_element():
    # A lang="" attribute isn't split on whitespace by BeautifulSoup the way a
    # class token is, so a hostile page can put a backtick run in it that would
    # otherwise extend/break the ``` fence and splice attacker-controlled text
    # into the surrounding document as live Markdown.
    content = _content(
        html='<pre><code lang="python```\n# injected heading">x = 1</code></pre>'
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "```python" in md
    assert "x = 1" in md
    assert md.count("```") == 2
    assert "\n# injected heading" not in md


def test_convert_strips_newline_from_lang_attribute_on_bare_pre():
    # A newline in lang="" ends the fence's info-string line early, handing the
    # rest of the line to the block-level Markdown parser instead of leaving it
    # inert inside the fence.
    content = _content(html='<pre lang="python\n# injected heading">print(1)</pre>')
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "```python" in md
    assert "print(1)" in md
    assert md.count("```") == 2
    assert "\n# injected heading" not in md


# ---------------------------------------------------------------------------
# title_to_hashtag_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Python", "python"),
        ("Machine Learning", "machine_learning"),
        ("Multi  Spaces", "multi_spaces"),
        ("  Padded  ", "padded"),
        ("Café", "café"),
        ("Well-Being", "well-being"),
        ("Don't Stop", "don't_stop"),
        ("already_snake", "already_snake"),
    ],
)
def test_title_to_hashtag_key(raw, expected):
    assert title_to_hashtag_key(raw) == expected


def test_title_to_hashtag_key_distinguishes_accents():
    # #café and #cafe are different hashtag identities — an accent-stripping
    # comparison would incorrectly collapse them to the same key.
    assert title_to_hashtag_key("Café") != title_to_hashtag_key("Cafe")


def test_title_to_hashtag_key_preserves_symbols():
    # A hyphen is part of the hashtag charset — it must not fold to
    # underscore the way an aggressive ASCII slugifier would.
    assert title_to_hashtag_key("Well-Being") != "well_being"


# ---------------------------------------------------------------------------
# update_linked
# ---------------------------------------------------------------------------

_FM_BASE = "---\ntitle: Alpha\nurl: https://example.com\nstatus: unread\n---\n\nBody.\n"


def test_update_linked_add_first():
    result = update_linked(_FM_BASE, add="Beta")
    data = yaml.safe_load(result.split("---\n", 2)[1])
    assert data["linked"] == ["Beta"]


def test_update_linked_add_appends():
    md = "---\ntitle: A\nlinked:\n- Beta\n---\n\nBody.\n"
    result = update_linked(md, add="Gamma")
    data = yaml.safe_load(result.split("---\n", 2)[1])
    assert data["linked"] == ["Beta", "Gamma"]


def test_update_linked_add_idempotent():
    md = "---\ntitle: A\nlinked:\n- Beta\n---\n\nBody.\n"
    result = update_linked(md, add="Beta")
    data = yaml.safe_load(result.split("---\n", 2)[1])
    assert data["linked"] == ["Beta"]


def test_update_linked_remove_present():
    md = "---\ntitle: A\nlinked:\n- Beta\n- Gamma\n---\n\nBody.\n"
    result = update_linked(md, remove="Beta")
    data = yaml.safe_load(result.split("---\n", 2)[1])
    assert data["linked"] == ["Gamma"]


def test_update_linked_remove_last_drops_field():
    md = "---\ntitle: A\nlinked:\n- Beta\n---\n\nBody.\n"
    result = update_linked(md, remove="Beta")
    data = yaml.safe_load(result.split("---\n", 2)[1])
    assert "linked" not in data


def test_update_linked_remove_absent_is_noop():
    md = "---\ntitle: A\nlinked:\n- Beta\n---\n\nBody.\n"
    result = update_linked(md, remove="Missing")
    data = yaml.safe_load(result.split("---\n", 2)[1])
    assert data["linked"] == ["Beta"]


def test_update_linked_no_frontmatter_returns_unchanged():
    md = "No frontmatter here.\n"
    assert update_linked(md, add="Beta") == md


def test_update_linked_preserves_body():
    result = update_linked(_FM_BASE, add="Beta")
    assert result.endswith("\nBody.\n")
