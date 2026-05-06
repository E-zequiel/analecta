"""Tests for M12 settings panel."""

from unittest.mock import MagicMock, patch

import pytest

from analecta.config import AppConfig, save_config
from analecta.ui.settings import _KEYRING_KEY, _KEYRING_SERVICE, SettingsPanel


@pytest.fixture
def config():
    return AppConfig()


@pytest.fixture
def config_vt():
    return AppConfig(virustotal_enabled=True)


# ---------------------------------------------------------------------------
# save_config (pure, no Qt)
# ---------------------------------------------------------------------------


def test_save_config_creates_file(tmp_path):
    cfg = AppConfig(vault_path=tmp_path / "vault")
    dest = tmp_path / "config.toml"
    save_config(cfg, dest)
    assert dest.exists()


def test_save_config_roundtrip(tmp_path):
    from analecta.config import load_config

    cfg = AppConfig(
        vault_path=tmp_path / "vault",
        font_variant="nerd",
        update_channel="dev",
        virustotal_enabled=True,
    )
    dest = tmp_path / "config.toml"
    save_config(cfg, dest)
    loaded = load_config(dest)
    assert loaded.font_variant == "nerd"
    assert loaded.update_channel == "dev"
    assert loaded.virustotal_enabled is True


def test_save_config_creates_parent_dirs(tmp_path):
    cfg = AppConfig()
    dest = tmp_path / "a" / "b" / "config.toml"
    save_config(cfg, dest)
    assert dest.exists()


# ---------------------------------------------------------------------------
# SettingsPanel — construction
# ---------------------------------------------------------------------------


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
def test_settings_creates(mock_kr, qtbot, config):
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    assert panel is not None


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
def test_settings_populates_vault_path(mock_kr, qtbot, config, tmp_path):
    cfg = AppConfig(vault_path=tmp_path / "vault")
    panel = SettingsPanel(cfg)
    qtbot.addWidget(panel)
    assert str(tmp_path / "vault") in panel._vault_edit.text()


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
def test_settings_populates_font_variant(mock_kr, qtbot):
    panel = SettingsPanel(AppConfig(font_variant="nerd"))
    qtbot.addWidget(panel)
    assert panel._font_combo.currentData() == "nerd"


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
def test_settings_populates_update_channel(mock_kr, qtbot):
    panel = SettingsPanel(AppConfig(update_channel="dev"))
    qtbot.addWidget(panel)
    assert panel._channel_combo.currentData() == "dev"


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
def test_settings_vt_unchecked_by_default(mock_kr, qtbot, config):
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    assert not panel._vt_check.isChecked()


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
def test_settings_vt_checked_when_enabled(mock_kr, qtbot, config_vt):
    panel = SettingsPanel(config_vt)
    qtbot.addWidget(panel)
    assert panel._vt_check.isChecked()


@patch("analecta.ui.settings.keyring.get_password", return_value="secret_key")
def test_settings_populates_api_key_from_keyring(mock_kr, qtbot, config):
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    assert panel._vt_key_edit.text() == "secret_key"


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
def test_settings_api_key_echo_mode_is_password(mock_kr, qtbot, config):
    from PySide6.QtWidgets import QLineEdit

    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    assert panel._vt_key_edit.echoMode() == QLineEdit.EchoMode.Password


# ---------------------------------------------------------------------------
# SettingsPanel — VT disclaimer
# ---------------------------------------------------------------------------


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.QMessageBox.question", return_value=MagicMock())
def test_vt_disclaimer_shown_on_first_enable(mock_qmb, mock_kr, qtbot, config):
    from PySide6.QtWidgets import QMessageBox

    mock_qmb.return_value = QMessageBox.StandardButton.No
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    panel._vt_check.setChecked(True)
    mock_qmb.assert_called_once()


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.QMessageBox.question")
def test_vt_declined_reverts_checkbox(mock_qmb, mock_kr, qtbot, config):
    from PySide6.QtWidgets import QMessageBox

    mock_qmb.return_value = QMessageBox.StandardButton.No
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    panel._vt_check.setChecked(True)
    assert not panel._vt_check.isChecked()


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.QMessageBox.question")
def test_vt_accepted_keeps_checkbox(mock_qmb, mock_kr, qtbot, config):
    from PySide6.QtWidgets import QMessageBox

    mock_qmb.return_value = QMessageBox.StandardButton.Yes
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    panel._vt_check.setChecked(True)
    assert panel._vt_check.isChecked()


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
def test_vt_no_disclaimer_when_already_enabled(mock_kr, qtbot, config_vt):
    with patch("analecta.ui.settings.QMessageBox.question") as mock_qmb:
        panel = SettingsPanel(config_vt)
        qtbot.addWidget(panel)
        panel._vt_check.setChecked(False)
        panel._vt_check.setChecked(True)
        mock_qmb.assert_not_called()


# ---------------------------------------------------------------------------
# SettingsPanel — save
# ---------------------------------------------------------------------------


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.keyring.set_password")
@patch("analecta.ui.settings.save_config")
def test_save_emits_config_saved(mock_save, mock_set, mock_get, qtbot, config):
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    signals = []
    panel.config_saved.connect(signals.append)
    panel._save()
    assert len(signals) == 1
    assert isinstance(signals[0], AppConfig)


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.keyring.set_password")
@patch("analecta.ui.settings.save_config")
def test_save_calls_save_config(mock_save, mock_set, mock_get, qtbot, config):
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    panel._save()
    mock_save.assert_called_once()


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.keyring.set_password")
@patch("analecta.ui.settings.save_config")
def test_save_writes_api_key_to_keyring(mock_save, mock_set, mock_get, qtbot, config):
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    panel._vt_key_edit.setText("my_secret_key")
    panel._save()
    mock_set.assert_called_once_with(_KEYRING_SERVICE, _KEYRING_KEY, "my_secret_key")


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.keyring.set_password")
@patch("analecta.ui.settings.save_config")
def test_save_skips_keyring_when_key_empty(
    mock_save, mock_set, mock_get, qtbot, config
):
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    panel._vt_key_edit.setText("")
    panel._save()
    mock_set.assert_not_called()


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.save_config")
def test_cancel_emits_cancelled(mock_save, mock_get, qtbot, config):
    panel = SettingsPanel(config)
    qtbot.addWidget(panel)
    signals = []
    panel.cancelled.connect(lambda: signals.append(True))
    panel.cancelled.emit()
    assert signals == [True]


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.keyring.set_password")
@patch("analecta.ui.settings.save_config")
def test_save_reflects_font_variant(mock_save, mock_set, mock_get, qtbot):
    panel = SettingsPanel(AppConfig(font_variant="nerd"))
    qtbot.addWidget(panel)
    signals = []
    panel.config_saved.connect(signals.append)
    panel._save()
    assert signals[0].font_variant == "nerd"


@patch("analecta.ui.settings.keyring.get_password", return_value=None)
@patch("analecta.ui.settings.keyring.set_password")
@patch("analecta.ui.settings.save_config")
def test_save_reflects_update_channel(mock_save, mock_set, mock_get, qtbot):
    panel = SettingsPanel(AppConfig(update_channel="dev"))
    qtbot.addWidget(panel)
    signals = []
    panel.config_saved.connect(signals.append)
    panel._save()
    assert signals[0].update_channel == "dev"
