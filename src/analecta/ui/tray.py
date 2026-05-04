"""System tray icon — M13 UI."""

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from analecta.config import AppConfig
from analecta.ui.theme import ACCENT

_AUTOSTART_DIR = Path.home() / ".config" / "autostart"
_AUTOSTART_FILE = _AUTOSTART_DIR / "analecta.desktop"

_AUTOSTART_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Analecta
Exec={exec}
X-GNOME-Autostart-enabled=true
"""


def _make_icon() -> QIcon:
    px = QPixmap(22, 22)
    px.fill(QColor(0, 0, 0, 0))
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(ACCENT))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 18, 18)
    painter.end()
    return QIcon(px)


def _get_exec_path() -> str:
    """Return the path to the installed ``analecta`` executable.

    Returns:
        Absolute path from ``PATH`` lookup, or ``sys.argv[0]`` as fallback.
    """
    return shutil.which("analecta") or sys.argv[0]


def _is_autostart_enabled() -> bool:
    """Return True if the autostart desktop entry exists.

    Returns:
        ``True`` when ``~/.config/autostart/analecta.desktop`` is present.
    """
    return _AUTOSTART_FILE.exists()


def _write_autostart() -> None:
    """Create the autostart desktop entry for the current user."""
    _AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    _AUTOSTART_FILE.write_text(
        _AUTOSTART_ENTRY.format(exec=_get_exec_path()), encoding="utf-8"
    )


def _remove_autostart() -> None:
    """Remove the autostart desktop entry if it exists."""
    _AUTOSTART_FILE.unlink(missing_ok=True)


class SystemTray(QSystemTrayIcon):
    """Application system tray icon with context menu.

    Provides quick access to common actions and balloon notifications for
    extraction results. Double-clicking the icon emits ``open_requested``.

    Signals:
        add_url_requested: Clipboard contained a valid URL. Carries the URL.
        open_requested: User selected "Open Analecta" or double-clicked.
        quit_requested: User selected "Quit".

    Args:
        config: Current application configuration.
        parent: Parent QObject.
    """

    add_url_requested = Signal(str)
    open_requested = Signal()
    quit_requested = Signal()

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(_make_icon(), parent)
        self._config = config
        self.setToolTip("Analecta")
        self._build_menu()
        self.activated.connect(self._on_activated)
        self.show()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_success(self, title: str, message: str) -> None:
        """Show an information balloon notification.

        Args:
            title: Notification title.
            message: Notification body.
        """
        self.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 4000
        )

    def notify_error(self, title: str, message: str) -> None:
        """Show a critical balloon notification.

        Args:
            title: Notification title.
            message: Notification body.
        """
        self.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Critical, 4000
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = QMenu()

        self._add_url_action = menu.addAction("Add URL from clipboard")
        self._add_url_action.triggered.connect(self._on_add_url)

        self._open_action = menu.addAction("Open Analecta")
        self._open_action.triggered.connect(self.open_requested.emit)

        menu.addSeparator()

        self._autostart_action = menu.addAction("Start with system")
        self._autostart_action.setCheckable(True)
        self._autostart_action.setChecked(_is_autostart_enabled())
        self._autostart_action.toggled.connect(self._on_autostart_toggled)

        menu.addSeparator()

        self._quit_action = menu.addAction("Quit")
        self._quit_action.triggered.connect(self.quit_requested.emit)

        self.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_requested.emit()

    def _on_add_url(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text.startswith(("http://", "https://")):
            self.add_url_requested.emit(text)
        else:
            self.showMessage(
                "Analecta",
                "Clipboard does not contain a valid URL.",
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )

    def _on_autostart_toggled(self, checked: bool) -> None:
        if checked:
            _write_autostart()
        else:
            _remove_autostart()
