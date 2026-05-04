"""Tests for M13 system tray."""

from unittest.mock import patch

import pytest

from analecta.config import AppConfig
from analecta.ui.tray import (
    SystemTray,
    _get_exec_path,
    _is_autostart_enabled,
    _make_icon,
    _remove_autostart,
    _write_autostart,
)


@pytest.fixture
def config():
    return AppConfig()


@pytest.fixture
def make_tray(config, qtbot):
    """Create SystemTray instances that unregister from DBus on teardown."""
    created = []

    def factory(**kwargs):
        t = SystemTray(config, **kwargs)
        created.append(t)
        return t

    yield factory
    for t in created:
        t.hide()


# ---------------------------------------------------------------------------
# Pure helpers (no Qt)
# ---------------------------------------------------------------------------


def test_make_icon_returns_valid_icon(qapp):
    from PySide6.QtGui import QIcon

    icon = _make_icon()
    assert isinstance(icon, QIcon)
    assert not icon.isNull()


def test_get_exec_path_returns_string():
    result = _get_exec_path()
    assert isinstance(result, str)
    assert len(result) > 0


@patch("analecta.ui.tray.shutil.which", return_value="/usr/local/bin/analecta")
def test_get_exec_path_prefers_which(mock_which):
    assert _get_exec_path() == "/usr/local/bin/analecta"


@patch("analecta.ui.tray.shutil.which", return_value=None)
def test_get_exec_path_fallback_to_argv(mock_which):
    import sys

    assert _get_exec_path() == sys.argv[0]


def test_is_autostart_enabled_false_when_missing(tmp_path):
    with patch("analecta.ui.tray._AUTOSTART_FILE", tmp_path / "missing.desktop"):
        assert _is_autostart_enabled() is False


def test_is_autostart_enabled_true_when_present(tmp_path):
    f = tmp_path / "analecta.desktop"
    f.write_text("[Desktop Entry]\n")
    with patch("analecta.ui.tray._AUTOSTART_FILE", f):
        assert _is_autostart_enabled() is True


def test_write_autostart_creates_file(tmp_path):
    dest = tmp_path / "analecta.desktop"
    with (
        patch("analecta.ui.tray._AUTOSTART_DIR", tmp_path),
        patch("analecta.ui.tray._AUTOSTART_FILE", dest),
        patch("analecta.ui.tray._get_exec_path", return_value="/bin/analecta"),
    ):
        _write_autostart()
    assert dest.exists()
    content = dest.read_text()
    assert "Exec=/bin/analecta" in content
    assert "X-GNOME-Autostart-enabled=true" in content


def test_write_autostart_creates_parent_dirs(tmp_path):
    parent = tmp_path / "a" / "b"
    dest = parent / "analecta.desktop"
    with (
        patch("analecta.ui.tray._AUTOSTART_DIR", parent),
        patch("analecta.ui.tray._AUTOSTART_FILE", dest),
        patch("analecta.ui.tray._get_exec_path", return_value="/bin/analecta"),
    ):
        _write_autostart()
    assert dest.exists()


def test_remove_autostart_deletes_file(tmp_path):
    dest = tmp_path / "analecta.desktop"
    dest.write_text("[Desktop Entry]\n")
    with patch("analecta.ui.tray._AUTOSTART_FILE", dest):
        _remove_autostart()
    assert not dest.exists()


def test_remove_autostart_no_error_when_missing(tmp_path):
    dest = tmp_path / "missing.desktop"
    with patch("analecta.ui.tray._AUTOSTART_FILE", dest):
        _remove_autostart()  # must not raise


# ---------------------------------------------------------------------------
# SystemTray — construction
# ---------------------------------------------------------------------------


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_tray_creates(mock_ae, make_tray):
    assert make_tray() is not None


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_tray_has_correct_tooltip(mock_ae, make_tray):
    assert make_tray().toolTip() == "Analecta"


@patch("analecta.ui.tray._is_autostart_enabled", return_value=True)
def test_tray_autostart_checked_when_enabled(mock_ae, make_tray):
    assert make_tray()._autostart_action.isChecked()


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_tray_autostart_unchecked_when_disabled(mock_ae, make_tray):
    assert not make_tray()._autostart_action.isChecked()


# ---------------------------------------------------------------------------
# SystemTray — signals
# ---------------------------------------------------------------------------


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_open_action_emits_open_requested(mock_ae, make_tray):
    tray = make_tray()
    received = []
    tray.open_requested.connect(lambda: received.append(True))
    tray._open_action.trigger()
    assert received == [True]


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_quit_action_emits_quit_requested(mock_ae, make_tray):
    tray = make_tray()
    received = []
    tray.quit_requested.connect(lambda: received.append(True))
    tray._quit_action.trigger()
    assert received == [True]


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_double_click_emits_open_requested(mock_ae, make_tray):
    from PySide6.QtWidgets import QSystemTrayIcon

    tray = make_tray()
    received = []
    tray.open_requested.connect(lambda: received.append(True))
    tray._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert received == [True]


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_single_click_does_not_emit_open_requested(mock_ae, make_tray):
    from PySide6.QtWidgets import QSystemTrayIcon

    tray = make_tray()
    received = []
    tray.open_requested.connect(lambda: received.append(True))
    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert received == []


# ---------------------------------------------------------------------------
# SystemTray — add URL from clipboard
# ---------------------------------------------------------------------------


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
@patch("analecta.ui.tray.QApplication.clipboard")
def test_add_url_emits_signal_for_http(mock_cb, mock_ae, make_tray):
    mock_cb.return_value.text.return_value = "http://example.com"
    tray = make_tray()
    received = []
    tray.add_url_requested.connect(received.append)
    tray._on_add_url()
    assert received == ["http://example.com"]


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
@patch("analecta.ui.tray.QApplication.clipboard")
def test_add_url_emits_signal_for_https(mock_cb, mock_ae, make_tray):
    mock_cb.return_value.text.return_value = "https://example.com/path?q=1"
    tray = make_tray()
    received = []
    tray.add_url_requested.connect(received.append)
    tray._on_add_url()
    assert received == ["https://example.com/path?q=1"]


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
@patch("analecta.ui.tray.QApplication.clipboard")
def test_add_url_strips_whitespace(mock_cb, mock_ae, make_tray):
    mock_cb.return_value.text.return_value = "  https://example.com  "
    tray = make_tray()
    received = []
    tray.add_url_requested.connect(received.append)
    tray._on_add_url()
    assert received == ["https://example.com"]


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
@patch("analecta.ui.tray.QApplication.clipboard")
def test_add_url_shows_warning_for_invalid(mock_cb, mock_ae, make_tray):
    mock_cb.return_value.text.return_value = "not a url"
    tray = make_tray()
    received = []
    tray.add_url_requested.connect(received.append)
    with patch.object(tray, "showMessage") as mock_msg:
        tray._on_add_url()
    assert received == []
    mock_msg.assert_called_once()


# ---------------------------------------------------------------------------
# SystemTray — autostart toggle
# ---------------------------------------------------------------------------


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
@patch("analecta.ui.tray._write_autostart")
def test_autostart_toggle_on_calls_write(mock_write, mock_ae, make_tray):
    make_tray()._on_autostart_toggled(True)
    mock_write.assert_called_once()


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
@patch("analecta.ui.tray._remove_autostart")
def test_autostart_toggle_off_calls_remove(mock_remove, mock_ae, make_tray):
    make_tray()._on_autostart_toggled(False)
    mock_remove.assert_called_once()


# ---------------------------------------------------------------------------
# SystemTray — notifications
# ---------------------------------------------------------------------------


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_notify_success_calls_show_message(mock_ae, make_tray):
    from PySide6.QtWidgets import QSystemTrayIcon

    tray = make_tray()
    with patch.object(tray, "showMessage") as mock_msg:
        tray.notify_success("Title", "Body")
    mock_msg.assert_called_once_with(
        "Title", "Body", QSystemTrayIcon.MessageIcon.Information, 4000
    )


@patch("analecta.ui.tray._is_autostart_enabled", return_value=False)
def test_notify_error_calls_show_message(mock_ae, make_tray):
    from PySide6.QtWidgets import QSystemTrayIcon

    tray = make_tray()
    with patch.object(tray, "showMessage") as mock_msg:
        tray.notify_error("Error", "Details")
    mock_msg.assert_called_once_with(
        "Error", "Details", QSystemTrayIcon.MessageIcon.Critical, 4000
    )
