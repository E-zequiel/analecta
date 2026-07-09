from typing import Any

import pytest
import yaml

from analecta.extraction.core import ExtractedContent
from analecta.markdown.converter import MarkdownConverter
from analecta.markdown.frontmatter import (
    build_frontmatter,
    build_template_block,
    update_linked,
)
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


# ---------------------------------------------------------------------------
# build_template_block
# ---------------------------------------------------------------------------


def test_template_block_contains_source_type():
    block = build_template_block("article")
    assert "analecta_article" in block


def test_template_block_has_template_property():
    block = build_template_block("youtube")
    assert "template::" in block
    assert "analecta_youtube" in block


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


def test_convert_resolves_nextjs_image_proxy():
    encoded = "https%3A%2F%2Fcdn.example.com%2Fphoto.jpg"
    content = _content(
        html=f'<img src="/_next/image?url={encoded}&w=1080&q=75" alt="photo">'
    )
    md = MarkdownConverter().convert(content, _CREATED_AT)
    assert "https://cdn.example.com/photo.jpg" in md
    assert "/_next/image" not in md


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
