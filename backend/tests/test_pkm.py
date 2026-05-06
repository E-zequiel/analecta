import pytest

from analecta.pkm.tags import get_backlinks, get_cooccurrences
from analecta.pkm.templates import list_template_pages, write_template_page
from analecta.pkm.url_scheme import (
    is_scheme_registered,
    make_url,
    parse_url,
    register_scheme,
)
from analecta.storage.index import EntryRecord, VaultIndex

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def index(tmp_path):
    with VaultIndex(tmp_path / "test.db") as idx:
        yield idx


def _entry(**kwargs) -> EntryRecord:
    defaults = dict(
        title="T",
        url="https://example.com/1",
        file_path="/vault/pages/2024-01-01-t.md",
        source_type="article",
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )
    defaults.update(kwargs)
    return EntryRecord(**defaults)


# ---------------------------------------------------------------------------
# VaultIndex.get_entry_ids_by_tag (new primitive)
# ---------------------------------------------------------------------------


def test_get_entry_ids_by_tag_returns_ids(index):
    eid = index.add_entry(_entry())
    index.update_tags(eid, ["python"])
    assert index.get_entry_ids_by_tag("python") == [eid]


def test_get_entry_ids_by_tag_missing_tag(index):
    assert index.get_entry_ids_by_tag("nonexistent") == []


def test_get_entry_ids_by_tag_multiple_entries(index):
    e1 = index.add_entry(_entry(url="https://a.com"))
    e2 = index.add_entry(_entry(url="https://b.com"))
    e3 = index.add_entry(_entry(url="https://c.com"))
    index.update_tags(e1, ["python", "ai"])
    index.update_tags(e2, ["python"])
    index.update_tags(e3, ["rust"])
    ids = index.get_entry_ids_by_tag("python")
    assert set(ids) == {e1, e2}


# ---------------------------------------------------------------------------
# tags — get_backlinks
# ---------------------------------------------------------------------------


def test_get_backlinks_returns_matching_ids(index):
    e1 = index.add_entry(_entry(url="https://a.com"))
    e2 = index.add_entry(_entry(url="https://b.com"))
    index.update_tags(e1, ["python"])
    index.update_tags(e2, ["rust"])
    assert get_backlinks("python", index) == [e1]


def test_get_backlinks_empty_when_no_match(index):
    assert get_backlinks("ghost", index) == []


def test_get_backlinks_multiple(index):
    e1 = index.add_entry(_entry(url="https://a.com"))
    e2 = index.add_entry(_entry(url="https://b.com"))
    index.update_tags(e1, ["python"])
    index.update_tags(e2, ["python", "ai"])
    assert set(get_backlinks("python", index)) == {e1, e2}


# ---------------------------------------------------------------------------
# tags — get_cooccurrences
# ---------------------------------------------------------------------------


def test_get_cooccurrences_counts(index):
    e1 = index.add_entry(_entry(url="https://a.com"))
    e2 = index.add_entry(_entry(url="https://b.com"))
    e3 = index.add_entry(_entry(url="https://c.com"))
    index.update_tags(e1, ["python", "ai"])
    index.update_tags(e2, ["python", "ml"])
    index.update_tags(e3, ["python", "ai"])
    result = get_cooccurrences("python", index)
    assert result == {"ai": 2, "ml": 1}


def test_get_cooccurrences_no_cooccurrences(index):
    e1 = index.add_entry(_entry())
    index.update_tags(e1, ["solo"])
    assert get_cooccurrences("solo", index) == {}


def test_get_cooccurrences_unknown_tag(index):
    assert get_cooccurrences("ghost", index) == {}


def test_get_cooccurrences_sorted_descending(index):
    for url in ["https://a.com", "https://b.com", "https://c.com"]:
        eid = index.add_entry(_entry(url=url))
        index.update_tags(eid, ["python", "ai"])
    e4 = index.add_entry(_entry(url="https://d.com"))
    index.update_tags(e4, ["python", "ml"])
    result = get_cooccurrences("python", index)
    keys = list(result.keys())
    assert keys[0] == "ai"
    assert keys[1] == "ml"


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------


def test_write_template_page_creates_file(tmp_path):
    path = write_template_page(tmp_path, "article")
    assert path.exists()
    assert path.name == "template-article.md"


def test_write_template_page_content_has_source_type(tmp_path):
    path = write_template_page(tmp_path, "youtube")
    assert "analecta_youtube" in path.read_text()


@pytest.mark.parametrize("source_type", ["article", "youtube", "substack", "x"])
def test_write_template_page_all_source_types(tmp_path, source_type):
    path = write_template_page(tmp_path, source_type)
    assert path.exists()


def test_list_template_pages_empty_vault(tmp_path):
    assert list_template_pages(tmp_path) == []


def test_list_template_pages_finds_written(tmp_path):
    write_template_page(tmp_path, "article")
    write_template_page(tmp_path, "youtube")
    pages = list_template_pages(tmp_path)
    names = [p.name for p in pages]
    assert "template-article.md" in names
    assert "template-youtube.md" in names


def test_list_template_pages_sorted(tmp_path):
    write_template_page(tmp_path, "youtube")
    write_template_page(tmp_path, "article")
    pages = list_template_pages(tmp_path)
    assert pages[0].name < pages[1].name


# ---------------------------------------------------------------------------
# url_scheme — make_url / parse_url
# ---------------------------------------------------------------------------


def test_make_url():
    assert make_url(42) == "analecta://open?id=42"


def test_make_url_entry_1():
    assert make_url(1) == "analecta://open?id=1"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("analecta://open?id=1", 1),
        ("analecta://open?id=42", 42),
        ("analecta://open?id=999", 999),
    ],
)
def test_parse_url_valid(url, expected):
    assert parse_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com?id=1",
        "analecta://other?id=1",
        "analecta://open",
        "analecta://open?id=",
        "analecta://open?id=abc",
        "analecta://open?id=-1",
        "analecta://open?id=0",
        "analecta://open?id=1;DROP TABLE entries--",
        "",
        "not-a-url",
    ],
)
def test_parse_url_invalid(url):
    assert parse_url(url) is None


def test_parse_url_roundtrip():
    assert parse_url(make_url(7)) == 7


# ---------------------------------------------------------------------------
# url_scheme — register_scheme
# ---------------------------------------------------------------------------


def test_register_scheme_writes_desktop_file(mocker, tmp_path):
    mocker.patch("subprocess.run")
    register_scheme("/usr/bin/analecta", desktop_dir=tmp_path)
    desktop = tmp_path / "analecta.desktop"
    assert desktop.exists()
    content = desktop.read_text()
    assert "x-scheme-handler/analecta" in content
    assert "/usr/bin/analecta" in content


def test_register_scheme_calls_xdg_mime(mocker, tmp_path):
    mock_run = mocker.patch("subprocess.run")
    register_scheme("/usr/bin/analecta", desktop_dir=tmp_path)
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "xdg-mime" in args
    assert "x-scheme-handler/analecta" in args


def test_register_scheme_creates_desktop_dir(mocker, tmp_path):
    mocker.patch("subprocess.run")
    nested = tmp_path / "a" / "b" / "applications"
    register_scheme("/usr/bin/analecta", desktop_dir=nested)
    assert nested.is_dir()


# ---------------------------------------------------------------------------
# url_scheme — is_scheme_registered
# ---------------------------------------------------------------------------


def test_is_scheme_registered_false_when_missing(tmp_path):
    assert is_scheme_registered(desktop_dir=tmp_path) is False


def test_is_scheme_registered_true_when_present(tmp_path):
    (tmp_path / "analecta.desktop").write_text("[Desktop Entry]\n")
    assert is_scheme_registered(desktop_dir=tmp_path) is True
