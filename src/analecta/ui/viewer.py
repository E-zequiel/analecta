"""Article viewer — M10 UI."""

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from analecta.config import AppConfig
from analecta.pkm.url_scheme import make_url
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.ui.theme import ACCENT, BG, BG_FLOAT, BG_HIGHLIGHT, BORDER, FG, FG_DIM

_STATUS_UNREAD = "unread"

_CSS = f"""\
body {{
    background: {BG};
    color: {FG};
    font-family: -apple-system, 'Segoe UI', 'Helvetica Neue', sans-serif;
    font-size: 15px;
    line-height: 1.7;
    max-width: 760px;
    margin: 0 auto;
    padding: 24px 32px;
}}
a {{ color: {ACCENT}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code, pre {{
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    background: {BG_FLOAT};
    border-radius: 4px;
}}
code {{ padding: 2px 5px; font-size: 0.875em; }}
pre {{ padding: 16px; overflow-x: auto; }}
pre code {{ background: none; padding: 0; }}
h1, h2, h3, h4 {{
    color: {FG};
    border-bottom: 1px solid {BORDER};
    padding-bottom: 6px;
    margin-top: 1.5em;
}}
blockquote {{
    border-left: 3px solid {ACCENT};
    margin: 0;
    padding: 4px 16px;
    color: {FG_DIM};
}}
img {{ max-width: 100%; height: auto; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid {BORDER}; padding: 6px 12px; text-align: left; }}
th {{ background: {BG_FLOAT}; }}
tr:nth-child(even) {{ background: {BG_HIGHLIGHT}; }}
hr {{ border: none; border-top: 1px solid {BORDER}; margin: 1.5em 0; }}
input[type=checkbox] {{ accent-color: {ACCENT}; }}
"""


def _wrap_html(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head>"
        "<meta charset='utf-8'>"
        f"<style>{_CSS}</style>"
        f"</head><body>{body}</body></html>"
    )


class MarkdownRenderer:
    """Converts Markdown (with optional YAML frontmatter) to a themed HTML page.

    Uses ``markdown-it-py`` with the commonmark preset plus front-matter,
    task-list, table, and linkify extensions. Frontmatter is consumed
    silently and does not appear in the rendered output.
    """

    def __init__(self) -> None:
        from markdown_it import MarkdownIt
        from mdit_py_plugins.front_matter import front_matter_plugin
        from mdit_py_plugins.tasklists import tasklists_plugin

        self._md = (
            MarkdownIt("commonmark", {"linkify": True})
            .use(front_matter_plugin)
            .use(tasklists_plugin)
            .enable("table")
        )

    def render(self, markdown: str) -> str:
        """Return a complete HTML document for *markdown*.

        Args:
            markdown: Raw Markdown string, optionally with YAML frontmatter.

        Returns:
            Full HTML document string.
        """
        return _wrap_html(self._md.render(markdown))


class ArticleViewer(QWidget):
    """Renders a vault entry's Markdown file in a QWebEngineView.

    Read-only by default. The Edit button emits ``entry_unlocked`` so M11
    can take over. Status toggle buttons (Read / Favorite / Recommend) are
    mutually exclusive and map directly to the ``status`` field in the DB;
    clicking an already-active button resets the entry to ``"unread"``.

    Signals:
        back_requested: User clicked the Back button.
        entry_unlocked: User clicked Edit. Carries the current EntryRecord.
        status_changed: A status toggle fired. Carries ``(entry_id, new_status)``.
        scan_requested: User requested a VirusTotal scan. Carries the EntryRecord.

    Args:
        config: Application configuration.
        index: Open VaultIndex instance.
        parent: Parent QWidget.
    """

    back_requested = Signal()
    entry_unlocked = Signal(object)
    status_changed = Signal(int, str)
    scan_requested = Signal(object)

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
        """Display *entry* in the viewer.

        Reads the Markdown file from disk, renders it to HTML, and syncs
        all toolbar toggle states to the entry's current status.

        Args:
            entry: Entry to display.
        """
        self._entry = entry
        self._sync_toolbar()
        try:
            md_text = Path(entry.file_path).read_text(encoding="utf-8")
        except OSError:
            self._web_view.setHtml(
                f"<body style='background:{BG};color:#ff757f;padding:2em'>"
                "<p>File not found.</p></body>"
            )
            return
        html = self._renderer.render(md_text)
        base_url = QUrl.fromLocalFile(str(Path(entry.file_path).parent) + "/")
        self._web_view.setHtml(html, base_url)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar())
        self._web_view = QWebEngineView()
        layout.addWidget(self._web_view)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("viewerToolbar")
        bar.setFixedHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(4)

        self._back_btn = _flat_btn("← Back")
        self._back_btn.clicked.connect(self.back_requested.emit)
        h.addWidget(self._back_btn)

        self._title_lbl = QLabel()
        self._title_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        h.addWidget(self._title_lbl)

        h.addStretch()

        self._edit_btn = _flat_btn("Edit")
        self._edit_btn.clicked.connect(
            lambda: self.entry_unlocked.emit(self._entry)
        )
        h.addWidget(self._edit_btn)

        h.addSpacing(8)

        self._copy_btn = _flat_btn("Copy URL")
        self._copy_btn.clicked.connect(self._copy_analecta_url)
        h.addWidget(self._copy_btn)

        self._browser_btn = _flat_btn("Open")
        self._browser_btn.clicked.connect(self._open_in_browser)
        h.addWidget(self._browser_btn)

        self._files_btn = _flat_btn("Files")
        self._files_btn.clicked.connect(self._open_in_filemanager)
        h.addWidget(self._files_btn)

        if self._config.virustotal_enabled:
            self._vt_btn: QPushButton | None = _flat_btn("VirusTotal")
            self._vt_btn.clicked.connect(
                lambda: self.scan_requested.emit(self._entry)
            )
            h.addWidget(self._vt_btn)
        else:
            self._vt_btn = None

        h.addSpacing(8)

        self._read_btn = _flat_btn("Read", checkable=True)
        self._read_btn.clicked.connect(
            lambda: self._toggle_status("read")
        )
        h.addWidget(self._read_btn)

        self._fav_btn = _flat_btn("Favorite", checkable=True)
        self._fav_btn.clicked.connect(
            lambda: self._toggle_status("favorite")
        )
        h.addWidget(self._fav_btn)

        self._rec_btn = _flat_btn("Recommend", checkable=True)
        self._rec_btn.clicked.connect(
            lambda: self._toggle_status("to_recommend")
        )
        h.addWidget(self._rec_btn)

        return bar

    def _sync_toolbar(self) -> None:
        if self._entry is None:
            return
        self._title_lbl.setText(self._entry.title)
        status = self._entry.status
        self._read_btn.setChecked(status == "read")
        self._fav_btn.setChecked(status == "favorite")
        self._rec_btn.setChecked(status == "to_recommend")

    def _toggle_status(self, target: str) -> None:
        if self._entry is None or self._entry.id is None:
            return
        new_status = target if self._entry.status != target else _STATUS_UNREAD
        self._index.update_status(self._entry.id, new_status)
        self._entry = self._index.get_entry(self._entry.id)
        self._sync_toolbar()
        self.status_changed.emit(self._entry.id, new_status)

    def _copy_analecta_url(self) -> None:
        if self._entry is None or self._entry.id is None:
            return
        QApplication.clipboard().setText(make_url(self._entry.id))

    def _open_in_browser(self) -> None:
        if self._entry is None:
            return
        QDesktopServices.openUrl(QUrl(self._entry.url))

    def _open_in_filemanager(self) -> None:
        if self._entry is None:
            return
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(Path(self._entry.file_path).parent))
        )


def _flat_btn(label: str, *, checkable: bool = False) -> QPushButton:
    btn = QPushButton(label)
    btn.setFlat(True)
    btn.setCheckable(checkable)
    return btn
