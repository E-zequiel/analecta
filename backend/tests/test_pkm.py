from typing import Any

import pytest

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
    defaults: dict[str, Any] = dict(
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


def test_get_entry_ids_by_tag_falls_back_to_content_hashtag(index, tmp_path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Great article about #python.\n", encoding="utf-8")
    eid = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(eid)

    # No structural tag named "python" exists — only the content hashtag.
    assert index.get_entry_ids_by_tag("python") == [eid]


def test_get_entry_ids_by_tag_unions_structural_and_content(index, tmp_path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    structural_id = index.add_entry(_entry(url="https://a.com"))
    index.update_tags(structural_id, ["python"])
    content_id = index.add_entry(_entry(url="https://b.com", file_path=str(src_file)))
    index.index_backlinks(content_id)

    # A tag that's structural on one entry and a content hashtag on
    # another resolves to both — not just the structural one.
    assert set(index.get_entry_ids_by_tag("python")) == {structural_id, content_id}


def test_get_entry_ids_by_tag_content_hashtag_case_insensitive(index, tmp_path):
    vault = tmp_path / "pages"
    vault.mkdir()
    src_file = vault / "article.md"
    src_file.write_text("Filed under #python.\n", encoding="utf-8")
    eid = index.add_entry(_entry(file_path=str(src_file)))
    index.index_backlinks(eid)

    assert index.get_entry_ids_by_tag("PYTHON") == [eid]


# ---------------------------------------------------------------------------
# url_scheme — make_url / parse_url
# ---------------------------------------------------------------------------


def test_make_url():
    assert make_url(42) == "analecta://open?id=42"


def test_make_url_entry_1():
    assert make_url(1) == "analecta://open?id=1"


@pytest.mark.parametrize(
    ("url", "expected"),
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
