"""Tests for M10 article viewer."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from analecta.config import AppConfig
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.ui.viewer import ArticleViewer, MarkdownRenderer, _wrap_html


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _entry(tmp_path: Path, **kwargs) -> EntryRecord:
    md_file = tmp_path / "test.md"
    if not md_file.exists():
        md_file.write_text("# Hello\n\nBody text.", encoding="utf-8")
    defaults = dict(
        title="Test Entry",
        url="https://example.com/test",
        file_path=str(md_file),
        source_type="article",
        created_at=_now(),
        updated_at=_now(),
    )
    defaults.update(kwargs)
    return EntryRecord(**defaults)


@pytest.fixture
def config():
    return AppConfig()


@pytest.fixture
def config_vt():
    return AppConfig(virustotal_enabled=True)


@pytest.fixture
def index(tmp_path: Path):
    db = VaultIndex(tmp_path / "vault.db")
    yield db
    db.close()


# ---------------------------------------------------------------------------
# MarkdownRenderer
# ---------------------------------------------------------------------------


def test_renderer_returns_html_document():
    r = MarkdownRenderer()
    result = r.render("# Hello")
    assert "<!DOCTYPE html>" in result
    assert "<h1>" in result


def test_renderer_strips_frontmatter():
    r = MarkdownRenderer()
    md = "---\ntitle: Test\nurl: https://x.com\n---\n\n# Body"
    result = r.render(md)
    assert "title:" not in result
    assert "<h1>" in result


def test_renderer_renders_table():
    r = MarkdownRenderer()
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = r.render(md)
    assert "<table" in result


def test_renderer_renders_task_list():
    r = MarkdownRenderer()
    md = "- [x] Done\n- [ ] Todo"
    result = r.render(md)
    assert 'type="checkbox"' in result


def test_renderer_applies_theme_css():
    r = MarkdownRenderer()
    result = r.render("hello")
    assert "#1a1b26" in result  # BG


def test_wrap_html_structure():
    html = _wrap_html("<p>hi</p>")
    assert html.startswith("<!DOCTYPE html>")
    assert "<p>hi</p>" in html


# ---------------------------------------------------------------------------
# ArticleViewer — construction & toolbar
# ---------------------------------------------------------------------------


def test_viewer_creates(qtbot, config, index):
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    assert v is not None


def test_viewer_has_back_button(qtbot, config, index):
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    assert v._back_btn is not None


def test_viewer_back_signal(qtbot, config, index):
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    signals = []
    v.back_requested.connect(lambda: signals.append(True))
    v._back_btn.click()
    assert signals == [True]


def test_viewer_vt_button_hidden_when_disabled(qtbot, config, index):
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    assert v._vt_btn is None


def test_viewer_vt_button_shown_when_enabled(qtbot, config_vt, index):
    v = ArticleViewer(config_vt, index)
    qtbot.addWidget(v)
    assert v._vt_btn is not None


def test_viewer_scan_signal(qtbot, config_vt, index, tmp_path):
    e = _entry(tmp_path)
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config_vt, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    signals = []
    v.scan_requested.connect(signals.append)
    v._vt_btn.click()
    assert len(signals) == 1
    assert signals[0].id == eid


def test_viewer_edit_signal(qtbot, config, index, tmp_path):
    e = _entry(tmp_path)
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    signals = []
    v.entry_unlocked.connect(signals.append)
    v._edit_btn.click()
    assert len(signals) == 1


# ---------------------------------------------------------------------------
# ArticleViewer — load_entry
# ---------------------------------------------------------------------------


def test_viewer_load_entry_updates_title(qtbot, config, index, tmp_path):
    e = _entry(tmp_path, title="My Article")
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    assert v._title_lbl.text() == "My Article"


def test_viewer_load_entry_missing_file(qtbot, config, index, tmp_path):
    e = _entry(tmp_path, file_path="/nonexistent/path/file.md")
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)  # must not raise


# ---------------------------------------------------------------------------
# ArticleViewer — status toggles
# ---------------------------------------------------------------------------


def test_viewer_toggle_read(qtbot, config, index, tmp_path):
    e = _entry(tmp_path)
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    v._read_btn.click()
    assert index.get_entry(eid).status == "read"
    assert v._read_btn.isChecked()


def test_viewer_toggle_favorite(qtbot, config, index, tmp_path):
    e = _entry(tmp_path)
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    v._fav_btn.click()
    assert index.get_entry(eid).status == "favorite"


def test_viewer_toggle_recommend(qtbot, config, index, tmp_path):
    e = _entry(tmp_path)
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    v._rec_btn.click()
    assert index.get_entry(eid).status == "to_recommend"


def test_viewer_toggle_double_resets_to_unread(qtbot, config, index, tmp_path):
    e = _entry(tmp_path)
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    v._read_btn.click()
    v._read_btn.click()
    assert index.get_entry(eid).status == "unread"
    assert not v._read_btn.isChecked()


def test_viewer_status_changed_signal(qtbot, config, index, tmp_path):
    e = _entry(tmp_path)
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    signals = []
    v.status_changed.connect(lambda eid, st: signals.append((eid, st)))
    v._fav_btn.click()
    assert signals == [(eid, "favorite")]


# ---------------------------------------------------------------------------
# ArticleViewer — clipboard
# ---------------------------------------------------------------------------


def test_viewer_copy_url(qtbot, config, index, tmp_path):
    from PySide6.QtWidgets import QApplication

    e = _entry(tmp_path)
    eid = index.add_entry(e)
    e = index.get_entry(eid)
    v = ArticleViewer(config, index)
    qtbot.addWidget(v)
    v.load_entry(e)
    v._copy_btn.click()
    clipboard = QApplication.clipboard().text()
    assert clipboard == f"analecta://open?id={eid}"
