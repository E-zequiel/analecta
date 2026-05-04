"""Tests for M11 article editor."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from analecta.config import AppConfig
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.ui.editor import (
    ArticleEditor,
    MarkdownHighlighter,
    _extract_tags,
    _strip_fm,
)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _make_md_file(tmp_path: Path, content: str = "# Hello\n\nBody text.") -> Path:
    f = tmp_path / "test.md"
    f.write_text(content, encoding="utf-8")
    return f


def _entry(tmp_path: Path, **kwargs) -> EntryRecord:
    md_file = _make_md_file(tmp_path)
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
def index(tmp_path: Path):
    db = VaultIndex(tmp_path / "vault.db")
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_tags_basic():
    assert _extract_tags("some text #python #ai") == ["python", "ai"]


def test_extract_tags_deduplicated():
    assert _extract_tags("#python text #python") == ["python"]


def test_extract_tags_ignores_url_fragments():
    assert _extract_tags("see https://example.com/page#section") == []


def test_extract_tags_ignores_headings():
    assert _extract_tags("# Heading\n## Sub") == []


def test_extract_tags_empty_content():
    assert _extract_tags("") == []


def test_extract_tags_skips_frontmatter():
    md = "---\nurl: https://x.com/page#frag\n---\n\nBody #python"
    assert _extract_tags(md) == ["python"]


def test_strip_fm_removes_frontmatter():
    md = "---\ntitle: Test\nurl: https://x.com\n---\n\n# Body"
    assert _strip_fm(md) == "# Body"


def test_strip_fm_no_frontmatter():
    md = "# Hello\n\nBody"
    assert _strip_fm(md) == md


def test_strip_fm_empty():
    assert _strip_fm("") == ""


# ---------------------------------------------------------------------------
# MarkdownHighlighter
# ---------------------------------------------------------------------------


def test_highlighter_creates(qtbot):
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    h = MarkdownHighlighter(doc)
    assert h is not None


def test_highlighter_does_not_crash_on_markdown(qtbot):
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    MarkdownHighlighter(doc)
    doc.setPlainText("# Heading\n\n**bold** `code` #tag\n\n> quote")


def test_highlighter_fence_state(qtbot):
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QPlainTextEdit

    editor = QPlainTextEdit()
    qtbot.addWidget(editor)
    MarkdownHighlighter(editor.document())
    editor.setPlainText("```python\ncode here\n```")


# ---------------------------------------------------------------------------
# ArticleEditor — construction & toolbar
# ---------------------------------------------------------------------------


def test_editor_creates(qtbot, config, index):
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    assert e is not None


def test_editor_has_back_button(qtbot, config, index):
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    assert e._back_btn is not None


def test_editor_close_signal(qtbot, config, index):
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    signals = []
    e.close_requested.connect(lambda: signals.append(True))
    e._back_btn.click()
    assert signals == [True]


def test_editor_has_save_and_revert_buttons(qtbot, config, index):
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    assert e._save_btn is not None
    assert e._revert_btn is not None


def test_editor_preview_hidden_by_default(qtbot, config, index):
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    assert not e._preview_view.isVisible()


def test_editor_preview_toggle_shows_view(qtbot, config, index):
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e._preview_btn.setChecked(True)
    assert not e._preview_view.isHidden()


def test_editor_preview_toggle_hides_view(qtbot, config, index):
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e._preview_btn.setChecked(True)
    e._preview_btn.setChecked(False)
    assert e._preview_view.isHidden()


# ---------------------------------------------------------------------------
# ArticleEditor — load_entry
# ---------------------------------------------------------------------------


def test_editor_load_entry_populates_text(qtbot, config, index, tmp_path):
    md_file = _make_md_file(tmp_path, "# Hello\n\nBody text.")
    entry = _entry(tmp_path, file_path=str(md_file))
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)
    assert "# Hello" in e._editor.toPlainText()


def test_editor_load_entry_shows_title(qtbot, config, index, tmp_path):
    entry = _entry(tmp_path, title="My Article")
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)
    assert e._title_lbl.text() == "My Article"


def test_editor_load_entry_missing_file(qtbot, config, index, tmp_path):
    entry = _entry(tmp_path, file_path="/nonexistent/path/file.md")
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)  # must not raise
    assert e._editor.toPlainText() == ""


# ---------------------------------------------------------------------------
# ArticleEditor — save
# ---------------------------------------------------------------------------


def test_editor_save_writes_file(qtbot, config, index, tmp_path):
    md_file = _make_md_file(tmp_path)
    entry = _entry(tmp_path, file_path=str(md_file))
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)
    e._editor.setPlainText("# Updated\n\nNew body. #python")
    e._save_btn.click()
    assert md_file.read_text(encoding="utf-8") == "# Updated\n\nNew body. #python"


def test_editor_save_updates_tags(qtbot, config, index, tmp_path):
    md_file = _make_md_file(tmp_path)
    entry = _entry(tmp_path, file_path=str(md_file))
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)
    e._editor.setPlainText("# Article\n\nContent. #python #ai")
    e._save_btn.click()
    saved = index.get_entry(eid)
    import json
    tags = json.loads(saved.tags_json)
    assert "python" in tags
    assert "ai" in tags


def test_editor_save_updates_fts(qtbot, config, index, tmp_path):
    md_file = _make_md_file(tmp_path)
    entry = _entry(tmp_path, file_path=str(md_file))
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)
    e._editor.setPlainText("# Article\n\nSearchable xyzquux content.")
    e._save_btn.click()
    results = index.search("xyzquux")
    assert any(r.id == eid for r in results)


def test_editor_save_emits_signal(qtbot, config, index, tmp_path):
    md_file = _make_md_file(tmp_path)
    entry = _entry(tmp_path, file_path=str(md_file))
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)
    signals = []
    e.saved.connect(signals.append)
    e._save_btn.click()
    assert len(signals) == 1
    assert signals[0].id == eid


# ---------------------------------------------------------------------------
# ArticleEditor — revert
# ---------------------------------------------------------------------------


def test_editor_revert_restores_content(qtbot, config, index, tmp_path):
    md_file = tmp_path / "original.md"
    md_file.write_text("# Original content", encoding="utf-8")
    entry = EntryRecord(
        title="Test", url="https://example.com", file_path=str(md_file),
        source_type="article", created_at=_now(), updated_at=_now(),
    )
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)
    e._editor.setPlainText("# Modified content")
    e._revert_btn.click()
    assert e._editor.toPlainText() == "# Original content"


def test_editor_revert_no_crash_missing_file(qtbot, config, index, tmp_path):
    entry = _entry(tmp_path, file_path="/nonexistent/path/file.md")
    eid = index.add_entry(entry)
    entry = index.get_entry(eid)
    e = ArticleEditor(config, index)
    qtbot.addWidget(e)
    e.load_entry(entry)
    e._revert_btn.click()  # must not raise
