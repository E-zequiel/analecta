"""Article editor — M11 UI."""

import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from analecta.config import AppConfig
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.ui.fonts import FONT_FAMILY
from analecta.ui.theme import ACCENT, CYAN, FG_DIM, GREEN, ORANGE
from analecta.ui.viewer import MarkdownRenderer

# Inline hashtag pattern: #snake_case preceded and followed by whitespace/boundary.
_TAG_RE = re.compile(r"(?<!\S)#([a-z][a-z0-9_]*)(?!\S)")

# YAML frontmatter block at the very start of a document.
_FM_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def _extract_tags(content: str) -> list[str]:
    """Return unique inline hashtag names from *content*, in order of appearance.

    Frontmatter is excluded before scanning so URL fragments (``#anchor``)
    inside the ``url:`` field are not captured.

    Args:
        content: Raw Markdown string.

    Returns:
        Deduplicated list of tag name strings (without the leading ``#``).
    """
    body = _strip_fm(content)
    return list(dict.fromkeys(_TAG_RE.findall(body)))


def _strip_fm(content: str) -> str:
    """Remove YAML frontmatter from the start of *content*.

    Args:
        content: Raw Markdown string (may or may not have frontmatter).

    Returns:
        Content with the leading ``---...---`` block removed, stripped.
    """
    return _FM_RE.sub("", content, count=1).strip()


def _make_fmt(
    color: str | None = None,
    bold: bool = False,
    italic: bool = False,
) -> QTextCharFormat:
    fmt = QTextCharFormat()
    if color:
        fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


class MarkdownHighlighter(QSyntaxHighlighter):
    """Basic Markdown syntax highlighter using the Tokyo Night palette.

    Handles single-line rules (headings, bold, italic, inline code, links,
    hashtags, blockquotes, frontmatter delimiters) and multi-line fenced code
    blocks via block state (0 = normal, 1 = inside fence).

    Args:
        document: The QTextDocument to highlight.
    """

    _FENCE_STATE = 1

    def __init__(self, document) -> None:
        super().__init__(document)
        self._fence_fmt = _make_fmt(color=FG_DIM)
        self._rules: list[tuple[re.Pattern, QTextCharFormat]] = [
            (re.compile(r"^#{1,6} .+"), _make_fmt(color=CYAN, bold=True)),
            (re.compile(r"\*\*.+?\*\*|__.+?__"), _make_fmt(bold=True)),
            (re.compile(r"(?<!\*)\*(?!\*).+?(?<!\*)\*(?!\*)"), _make_fmt(italic=True)),
            (re.compile(r"`[^`\n]+`"), _make_fmt(color=ORANGE)),
            (re.compile(r"\[.+?\]\(.+?\)"), _make_fmt(color=ACCENT)),
            (re.compile(r"(?<!\S)#[a-z][a-z0-9_]*(?!\S)"), _make_fmt(color=GREEN)),
            (re.compile(r"^> .+"), _make_fmt(color=FG_DIM, italic=True)),
            (re.compile(r"^---$"), _make_fmt(color=FG_DIM)),
        ]

    def highlightBlock(self, text: str) -> None:
        """Apply highlighting rules to *text* (one paragraph block)."""
        is_fence = text.strip().startswith("```")
        prev = self.previousBlockState()

        if is_fence:
            self.setFormat(0, len(text), self._fence_fmt)
            next_state = 0 if prev == self._FENCE_STATE else self._FENCE_STATE
            self.setCurrentBlockState(next_state)
            return

        if prev == self._FENCE_STATE:
            self.setFormat(0, len(text), self._fence_fmt)
            self.setCurrentBlockState(self._FENCE_STATE)
            return

        self.setCurrentBlockState(0)
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class ArticleEditor(QWidget):
    """Plain-text Markdown editor with optional live preview.

    Saves the ``.md`` file to disk and keeps the SQLite index in sync
    (tags + FTS content) on every explicit save.

    Signals:
        close_requested: User clicked the back button (no implicit save).
        saved: File was saved. Carries the refreshed ``EntryRecord``.

    Args:
        config: Application configuration.
        index: Open VaultIndex instance.
        parent: Parent QWidget.
    """

    close_requested = Signal()
    saved = Signal(object)

    def __init__(
        self,
        config: AppConfig,
        index: VaultIndex,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._index = index
        self._entry: EntryRecord | None = None
        self._renderer = MarkdownRenderer()
        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_entry(self, entry: EntryRecord) -> None:
        """Populate the editor with *entry*'s Markdown file content.

        Args:
            entry: Entry to edit.
        """
        self._entry = entry
        self._title_lbl.setText(entry.title)
        try:
            content = Path(entry.file_path).read_text(encoding="utf-8")
        except OSError:
            content = ""
        self._editor.setPlainText(content)
        self._editor.document().setModified(False)
        if self._preview_btn.isChecked():
            self._update_preview()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar())

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        font = QFont(FONT_FAMILY, 13)
        self._editor = QPlainTextEdit()
        self._editor.setFont(font)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        MarkdownHighlighter(self._editor.document())

        self._preview_view = QWebEngineView()
        self._preview_view.setVisible(False)

        self._splitter.addWidget(self._editor)
        self._splitter.addWidget(self._preview_view)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)

        layout.addWidget(self._splitter)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(400)
        self._preview_timer.timeout.connect(self._update_preview)
        self._editor.textChanged.connect(self._on_text_changed)

        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save_action.triggered.connect(self._save)
        self.addAction(save_action)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("editorToolbar")
        bar.setFixedHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(4)

        self._back_btn = _flat_btn("← Viewer")
        self._back_btn.clicked.connect(self.close_requested.emit)
        h.addWidget(self._back_btn)

        self._title_lbl = QLabel()
        self._title_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        h.addWidget(self._title_lbl)

        h.addStretch()

        self._preview_btn = _flat_btn("Preview", checkable=True)
        self._preview_btn.toggled.connect(self._toggle_preview)
        h.addWidget(self._preview_btn)

        h.addSpacing(8)

        self._save_btn = _flat_btn("Save")
        self._save_btn.clicked.connect(self._save)
        h.addWidget(self._save_btn)

        self._revert_btn = _flat_btn("Revert")
        self._revert_btn.clicked.connect(self._revert)
        h.addWidget(self._revert_btn)

        return bar

    def _toggle_preview(self, checked: bool) -> None:
        self._preview_view.setVisible(checked)
        if checked:
            self._update_preview()

    def _on_text_changed(self) -> None:
        if self._preview_btn.isChecked():
            self._preview_timer.start()

    def _update_preview(self) -> None:
        from PySide6.QtCore import QUrl

        content = self._editor.toPlainText()
        html = self._renderer.render(content)
        if self._entry:
            base = QUrl.fromLocalFile(str(Path(self._entry.file_path).parent) + "/")
        else:
            base = QUrl()
        self._preview_view.setHtml(html, base)

    def _save(self) -> None:
        if self._entry is None or self._entry.id is None:
            return
        content = self._editor.toPlainText()
        Path(self._entry.file_path).write_text(content, encoding="utf-8")
        tags = _extract_tags(content)
        self._index.update_tags(self._entry.id, tags)
        self._index.update_fts_content(
            self._entry.id, self._entry.title, _strip_fm(content)
        )
        self._entry = self._index.get_entry(self._entry.id)
        self._editor.document().setModified(False)
        self.saved.emit(self._entry)

    def _revert(self) -> None:
        if self._entry is None:
            return
        try:
            content = Path(self._entry.file_path).read_text(encoding="utf-8")
        except OSError:
            return
        self._editor.setPlainText(content)
        self._editor.document().setModified(False)


def _flat_btn(label: str, *, checkable: bool = False) -> QPushButton:
    btn = QPushButton(label)
    btn.setFlat(True)
    btn.setCheckable(checkable)
    return btn
