"""Settings panel — M12 UI."""

from pathlib import Path

import keyring
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from analecta.config import AppConfig, save_config

_KEYRING_SERVICE = "analecta"
_KEYRING_KEY = "VIRUSTOTAL_API_KEY"

_VT_DISCLAIMER = (
    "⚠ VIRUSTOTAL INTEGRATION\n"
    "URLs submitted are stored publicly. Do NOT send URLs containing "
    "private data, tokens, or credentials.\n\n"
    "This feature requires your own Public API Key and is for "
    "non-commercial use only. By enabling this, you agree to "
    "VirusTotal's Terms of Service (https://cloud.google.com/terms/secops) "
    "and Privacy Notice (https://cloud.google.com/terms/secops/privacy-notice).\n\n"
    "Enable VirusTotal scanning and configure API Key?"
)


class SettingsPanel(QWidget):
    """Application settings editor.

    Covers vault path, font variant, update channel, and VirusTotal
    integration (API key + one-time opt-in disclaimer).

    Signals:
        config_saved: Emits the new ``AppConfig`` after a successful save.
        cancelled: User clicked Cancel without saving.

    Args:
        config: Current application configuration.
        parent: Parent QWidget.
    """

    config_saved = Signal(object)
    cancelled = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._build_ui()
        self._populate(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_config(self, config: AppConfig) -> None:
        """Refresh all fields from *config*.

        Args:
            config: Configuration to display.
        """
        self._config = config
        self._populate(config)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        layout.addWidget(self._build_vault_group())
        layout.addWidget(self._build_appearance_group())
        layout.addWidget(self._build_updates_group())
        layout.addWidget(self._build_vt_group())
        layout.addStretch()
        layout.addLayout(self._build_buttons())

    def _build_vault_group(self) -> QGroupBox:
        box = QGroupBox("Vault")
        h = QHBoxLayout(box)
        self._vault_edit = QLineEdit()
        self._vault_edit.setPlaceholderText("Path to vault directory")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_vault)
        h.addWidget(self._vault_edit)
        h.addWidget(browse_btn)
        return box

    def _build_appearance_group(self) -> QGroupBox:
        box = QGroupBox("Appearance")
        h = QHBoxLayout(box)
        h.addWidget(QLabel("Font:"))
        self._font_combo = QComboBox()
        self._font_combo.addItem("JetBrains Mono", "regular")
        self._font_combo.addItem("JetBrains Mono Nerd Font", "nerd")
        h.addWidget(self._font_combo)
        h.addStretch()
        return box

    def _build_updates_group(self) -> QGroupBox:
        box = QGroupBox("Updates")
        h = QHBoxLayout(box)
        h.addWidget(QLabel("Channel:"))
        self._channel_combo = QComboBox()
        self._channel_combo.addItem("Stable", "stable")
        self._channel_combo.addItem("Dev", "dev")
        h.addWidget(self._channel_combo)
        h.addStretch()
        return box

    def _build_vt_group(self) -> QGroupBox:
        box = QGroupBox("VirusTotal")
        v = QVBoxLayout(box)

        self._vt_check = QCheckBox("Enable VirusTotal URL scanning")
        self._vt_check.toggled.connect(self._on_vt_toggled)
        v.addWidget(self._vt_check)

        h = QHBoxLayout()
        h.addWidget(QLabel("API Key:"))
        self._vt_key_edit = QLineEdit()
        self._vt_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._vt_key_edit.setPlaceholderText("Stored in system keyring")
        h.addWidget(self._vt_key_edit)
        v.addLayout(h)

        return box

    def _build_buttons(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancelled.emit)
        h.addWidget(cancel_btn)
        h.addWidget(save_btn)
        return h

    def _populate(self, config: AppConfig) -> None:
        self._vault_edit.setText(str(config.vault_path))

        idx = self._font_combo.findData(config.font_variant)
        self._font_combo.setCurrentIndex(max(idx, 0))

        idx = self._channel_combo.findData(config.update_channel)
        self._channel_combo.setCurrentIndex(max(idx, 0))

        self._vt_check.blockSignals(True)
        self._vt_check.setChecked(config.virustotal_enabled)
        self._vt_check.blockSignals(False)

        existing_key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY) or ""
        self._vt_key_edit.setText(existing_key)

    def _browse_vault(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Vault Directory",
            self._vault_edit.text() or str(Path.home()),
        )
        if chosen:
            self._vault_edit.setText(chosen)

    def _on_vt_toggled(self, checked: bool) -> None:
        if not checked:
            return
        if self._config.virustotal_enabled:
            return
        reply = QMessageBox.question(
            self,
            "VirusTotal — Privacy Notice",
            _VT_DISCLAIMER,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._vt_check.blockSignals(True)
            self._vt_check.setChecked(False)
            self._vt_check.blockSignals(False)

    def _save(self) -> None:
        new_config = AppConfig(
            vault_path=Path(self._vault_edit.text()),
            font_variant=self._font_combo.currentData(),
            update_channel=self._channel_combo.currentData(),
            virustotal_enabled=self._vt_check.isChecked(),
        )
        save_config(new_config)

        api_key = self._vt_key_edit.text().strip()
        if api_key:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY, api_key)

        self._config = new_config
        self.config_saved.emit(new_config)
