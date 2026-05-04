"""Tests for FirstRunDialog."""

from pathlib import Path
from unittest.mock import patch

from analecta.config import AppConfig
from analecta.ui.first_run import FirstRunDialog


def test_first_run_dialog_creates(qtbot):
    dlg = FirstRunDialog()
    qtbot.addWidget(dlg)
    assert dlg is not None


def test_first_run_dialog_title(qtbot):
    dlg = FirstRunDialog()
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Welcome to Analecta"


def test_first_run_dialog_prefills_default_path(qtbot):
    dlg = FirstRunDialog()
    qtbot.addWidget(dlg)
    assert dlg._path_edit.text() == str(AppConfig().vault_path)


def test_first_run_dialog_confirm_sets_result_config(qtbot):
    dlg = FirstRunDialog()
    qtbot.addWidget(dlg)
    dlg._path_edit.setText("/tmp/my-vault")
    dlg._confirm()
    assert dlg.result_config.vault_path == Path("/tmp/my-vault")


def test_first_run_dialog_confirm_accepts(qtbot):
    dlg = FirstRunDialog()
    qtbot.addWidget(dlg)
    with qtbot.waitSignal(dlg.accepted):
        dlg._confirm()


def test_first_run_dialog_result_config_is_appconfig(qtbot):
    dlg = FirstRunDialog()
    qtbot.addWidget(dlg)
    dlg._confirm()
    assert isinstance(dlg.result_config, AppConfig)


def test_first_run_dialog_browse_updates_path(qtbot):
    dlg = FirstRunDialog()
    qtbot.addWidget(dlg)
    with patch(
        "analecta.ui.first_run.QFileDialog.getExistingDirectory",
        return_value="/tmp/chosen-vault",
    ):
        dlg._browse()
    assert dlg._path_edit.text() == "/tmp/chosen-vault"


def test_first_run_dialog_browse_no_change_on_cancel(qtbot):
    dlg = FirstRunDialog()
    qtbot.addWidget(dlg)
    original = dlg._path_edit.text()
    with patch(
        "analecta.ui.first_run.QFileDialog.getExistingDirectory",
        return_value="",
    ):
        dlg._browse()
    assert dlg._path_edit.text() == original
