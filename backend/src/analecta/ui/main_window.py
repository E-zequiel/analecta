"""Main application window — M8 UI shell."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QSplitter, QStackedWidget, QWidget

from analecta.config import AppConfig

_SIDEBAR_WIDTH = 260
_WINDOW_WIDTH = 1280
_WINDOW_HEIGHT = 800


class MainWindow(QMainWindow):
    """Top-level application window.

    Layout: ``QSplitter(sidebar | QStackedWidget)``.
    The sidebar is a plain ``QWidget`` placeholder that M9 replaces with the
    full dashboard sidebar. The ``QStackedWidget`` hosts one page per module
    (dashboard, viewer, editor, settings).

    Args:
        config: Loaded application configuration.
    """

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle("Analecta")
        self.resize(_WINDOW_WIDTH, _WINDOW_HEIGHT)
        self._build_layout()
        self._build_shortcuts()

    # ------------------------------------------------------------------
    # Public API (consumed by M9–M12 to inject their widgets)
    # ------------------------------------------------------------------

    @property
    def sidebar(self) -> QWidget:
        """Sidebar pane (left side of the splitter)."""
        return self._sidebar

    @property
    def content(self) -> QStackedWidget:
        """Stacked content area (right side of the splitter)."""
        return self._content

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self._sidebar = QWidget()
        self._content = QStackedWidget()

        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._content)
        splitter.setSizes([_SIDEBAR_WIDTH, _WINDOW_WIDTH - _SIDEBAR_WIDTH])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready")

    def _build_shortcuts(self) -> None:
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)
