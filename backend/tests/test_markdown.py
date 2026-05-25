import pytest
import yaml

from analecta.extraction.core import ExtractedContent
from analecta.markdown.converter import MarkdownConverter
from analecta.markdown.frontmatter import build_frontmatter, build_template_block
from analecta.markdown.hashtags import append_tags, find_heading_hashtags, normalize_tag

_CREATED_AT = "2024-01-15T10:00:00"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content(**kwargs) -> ExtractedContent:
    defaults = {
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


# ---------------------------------------------------------------------------
# normalize_tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Python", "python"),
        ("machine learning", "machine_learning"),
        ("NLP/ML", "nlp_ml"),
        ("C++", "c"),
        ("Éclair", "eclair"),
        ("  spaces  ", "spaces"),
        ("already_snake", "already_snake"),
        ("Multi  Spaces", "multi_spaces"),
    ],
)
def test_normalize_tag(raw, expected):
    assert normalize_tag(raw) == expected


def test_normalize_tag_empty_string():
    assert normalize_tag("") == ""


def test_normalize_tag_only_special_chars():
    assert normalize_tag("+++") == ""


# ---------------------------------------------------------------------------
# append_tags
# ---------------------------------------------------------------------------


def test_append_tags_adds_hash_prefix():
    result = append_tags("Body text", ["python", "ai"])
    assert "#python" in result
    assert "#ai" in result


def test_append_tags_on_separate_line():
    result = append_tags("Body text", ["tag"])
    lines = result.strip().splitlines()
    assert lines[-1] == "#tag"


def test_append_tags_empty_list_unchanged():
    md = "Body text"
    assert append_tags(md, []) == md


def test_append_tags_normalizes_tags():
    result = append_tags("Body", ["Machine Learning"])
    assert "#machine_learning" in result


def test_append_tags_skips_empty_normalized():
    result = append_tags("Body", ["+++", "python"])
    assert "#python" in result
    assert "#++" not in result


def test_append_tags_all_invalid_unchanged():
    md = "Body"
    assert append_tags(md, ["+++", "---"]) == md


# ---------------------------------------------------------------------------
# find_heading_hashtags
# ---------------------------------------------------------------------------


def test_find_heading_hashtags_detects_pattern():
    md = "Some text\n##BadTag here"
    found = find_heading_hashtags(md)
    assert any("##BadTag" in f for f in found)


def test_find_heading_hashtags_clean_document():
    md = "## Proper Heading\n### Another\n\nBody text #goodtag"
    assert find_heading_hashtags(md) == []


def test_find_heading_hashtags_ignores_inline():
    md = "Inline ##word in a paragraph"
    assert find_heading_hashtags(md) == []


def test_find_heading_hashtags_multiple():
    md = "##First\n## OK heading\n##Second"
    found = find_heading_hashtags(md)
    assert len(found) == 2
