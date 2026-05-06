"""Update checker — M14."""

import asyncio
import importlib.metadata
import logging
import os
import subprocess
import sys

import httpx
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from analecta.config import AppConfig

_log = logging.getLogger(__name__)
_PYPI_URL = "https://pypi.org/pypi/analecta/json"
_PACKAGE = "analecta"


def get_current_version() -> str:
    """Return the installed version of the package.

    Falls back to ``analecta.__version__`` if the package metadata is absent
    (e.g. editable installs without a build step).

    Returns:
        Version string such as ``"0.1.0"``.
    """
    try:
        return importlib.metadata.version(_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        from analecta import __version__

        return __version__


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def is_update_available(current: str, latest: str) -> bool:
    """Return True if *latest* is strictly newer than *current*.

    Args:
        current: Currently installed version string.
        latest: Latest available version string.

    Returns:
        ``True`` when *latest* is strictly newer than *current*.
    """
    return _parse_version(latest) > _parse_version(current)


async def fetch_latest_version(client: httpx.AsyncClient) -> str | None:
    """Query PyPI for the latest published version of the package.

    Args:
        client: Async HTTP client to use for the request.

    Returns:
        Latest version string, or ``None`` on any error.
    """
    try:
        resp = await client.get(_PYPI_URL, timeout=10.0)
        resp.raise_for_status()
        return resp.json()["info"]["version"]
    except Exception:
        _log.debug("Update check failed", exc_info=True)
        return None


def run_upgrade() -> bool:
    """Run ``uv tool upgrade analecta`` and return whether it succeeded.

    Returns:
        ``True`` if the upgrade command exited with code 0.
    """
    result = subprocess.run(
        ["uv", "tool", "upgrade", _PACKAGE],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _log.error("uv tool upgrade failed: %s", result.stderr)
    return result.returncode == 0


def restart() -> None:
    """Re-exec the current process to apply the update."""
    os.execv(sys.argv[0], sys.argv)


class UpdateDialog(QDialog):
    """Modal shown when a newer version of Analecta is available.

    Offers "Update Now" (runs ``uv tool upgrade``, then prompts restart)
    and "Later" actions.

    Args:
        current: Currently installed version string.
        latest: Latest available version string.
        parent: Parent QWidget.
    """

    def __init__(
        self, current: str, latest: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._current = current
        self._latest = latest
        self.setWindowTitle("Update Available")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        layout.addWidget(
            QLabel(
                f"<b>Analecta {self._latest}</b> is available.<br>"
                f"You have version {self._current}."
            )
        )

        self._status_label = QLabel("")
        self._status_label.hide()
        layout.addWidget(self._status_label)

        buttons = QDialogButtonBox()
        self._update_btn = buttons.addButton(
            "Update Now", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._later_btn = buttons.addButton(
            "Later", QDialogButtonBox.ButtonRole.RejectRole
        )
        self._update_btn.clicked.connect(self._on_update)
        self._later_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _on_update(self) -> None:
        self._update_btn.setEnabled(False)
        self._later_btn.setEnabled(False)
        self._status_label.setText("Updating…")
        self._status_label.show()
        asyncio.ensure_future(self._do_upgrade())

    async def _do_upgrade(self) -> None:
        success = await asyncio.to_thread(run_upgrade)
        if success:
            self._status_label.setText("Update complete.")
            reply = QMessageBox.question(
                self,
                "Restart Required",
                "Analecta has been updated. Restart now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            self.accept()
            if reply == QMessageBox.StandardButton.Yes:
                restart()
        else:
            self._status_label.setText(
                "Update failed. Run `uv tool upgrade analecta` manually."
            )
            self._later_btn.setEnabled(True)


async def check_and_notify(config: AppConfig, parent: QWidget | None = None) -> None:
    """Check PyPI for a newer version and show UpdateDialog if one exists.

    Silently skips on network errors so startup is never blocked.

    Args:
        config: Current application configuration.
        parent: Parent widget for the dialog.
    """
    current = get_current_version()
    async with httpx.AsyncClient() as client:
        latest = await fetch_latest_version(client)
    if latest is None:
        return
    if is_update_available(current, latest):
        _log.info("Update available: %s → %s", current, latest)
        dialog = UpdateDialog(current, latest, parent)
        dialog.exec()
