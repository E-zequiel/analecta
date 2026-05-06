"""Tests for M14 update checker."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from analecta.config import AppConfig
from analecta.updater.checker import (
    UpdateDialog,
    _parse_version,
    check_and_notify,
    fetch_latest_version,
    get_current_version,
    is_update_available,
    run_upgrade,
)


@pytest.fixture
def config():
    return AppConfig()


# ---------------------------------------------------------------------------
# get_current_version
# ---------------------------------------------------------------------------


def test_get_current_version_returns_string():
    v = get_current_version()
    assert isinstance(v, str)
    assert len(v) > 0


def test_get_current_version_from_metadata():
    with patch(
        "analecta.updater.checker.importlib.metadata.version", return_value="1.2.3"
    ):
        assert get_current_version() == "1.2.3"


def test_get_current_version_fallback_to_dunder():
    from importlib.metadata import PackageNotFoundError

    with patch(
        "analecta.updater.checker.importlib.metadata.version",
        side_effect=PackageNotFoundError,
    ):
        v = get_current_version()
    assert isinstance(v, str)


# ---------------------------------------------------------------------------
# _parse_version / is_update_available
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("0.1.0", (0, 1, 0)),
        ("10.0.0", (10, 0, 0)),
        ("not-a-version", (0,)),
    ],
)
def test_parse_version(v, expected):
    assert _parse_version(v) == expected


@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [
        ("0.1.0", "0.2.0", True),
        ("0.1.0", "1.0.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.2.0", "0.1.0", False),
        ("1.0.0", "0.9.9", False),
    ],
)
def test_is_update_available(current, latest, expected):
    assert is_update_available(current, latest) is expected


# ---------------------------------------------------------------------------
# fetch_latest_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_latest_version_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"info": {"version": "0.9.0"}}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await fetch_latest_version(mock_client)
    assert result == "0.9.0"


@pytest.mark.asyncio
async def test_fetch_latest_version_http_error():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))

    result = await fetch_latest_version(mock_client)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_latest_version_bad_json():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {}  # missing "info" key

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await fetch_latest_version(mock_client)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_latest_version_status_error():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await fetch_latest_version(mock_client)
    assert result is None


# ---------------------------------------------------------------------------
# run_upgrade
# ---------------------------------------------------------------------------


def test_run_upgrade_success():
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch(
        "analecta.updater.checker.subprocess.run", return_value=mock_result
    ) as mock_run:
        assert run_upgrade() is True
    mock_run.assert_called_once_with(
        ["uv", "tool", "upgrade", "analecta"],
        capture_output=True,
        text=True,
    )


def test_run_upgrade_failure():
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "error: package not found"
    with patch("analecta.updater.checker.subprocess.run", return_value=mock_result):
        assert run_upgrade() is False


# ---------------------------------------------------------------------------
# UpdateDialog — construction
# ---------------------------------------------------------------------------


def test_update_dialog_creates(qtbot):
    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Update Available"


def test_update_dialog_update_btn_enabled(qtbot):
    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)
    assert dialog._update_btn.isEnabled()


def test_update_dialog_later_btn_enabled(qtbot):
    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)
    assert dialog._later_btn.isEnabled()


def test_update_dialog_status_hidden(qtbot):
    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)
    assert dialog._status_label.isHidden()


def test_update_dialog_on_update_disables_buttons(qtbot):
    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)
    with patch("asyncio.ensure_future", side_effect=lambda coro: coro.close()):
        dialog._on_update()
    assert not dialog._update_btn.isEnabled()
    assert not dialog._later_btn.isEnabled()


def test_update_dialog_on_update_shows_status(qtbot):
    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)
    with patch("asyncio.ensure_future", side_effect=lambda coro: coro.close()):
        dialog._on_update()
    assert not dialog._status_label.isHidden()


# ---------------------------------------------------------------------------
# UpdateDialog — _do_upgrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_upgrade_success_prompts_restart(qtbot):
    from PySide6.QtWidgets import QMessageBox

    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)

    with (
        patch("analecta.updater.checker.run_upgrade", return_value=True),
        patch(
            "analecta.updater.checker.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as mock_qmb,
        patch("analecta.updater.checker.restart"),
    ):
        await dialog._do_upgrade()

    mock_qmb.assert_called_once()


@pytest.mark.asyncio
async def test_do_upgrade_success_restarts_when_confirmed(qtbot):
    from PySide6.QtWidgets import QMessageBox

    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)

    with (
        patch("analecta.updater.checker.run_upgrade", return_value=True),
        patch(
            "analecta.updater.checker.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ),
        patch("analecta.updater.checker.restart") as mock_restart,
    ):
        await dialog._do_upgrade()

    mock_restart.assert_called_once()


@pytest.mark.asyncio
async def test_do_upgrade_failure_shows_error(qtbot):
    dialog = UpdateDialog("0.1.0", "0.2.0")
    qtbot.addWidget(dialog)

    with patch("analecta.updater.checker.run_upgrade", return_value=False):
        await dialog._do_upgrade()

    assert "failed" in dialog._status_label.text().lower()
    assert dialog._later_btn.isEnabled()


# ---------------------------------------------------------------------------
# check_and_notify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_notify_no_dialog_when_up_to_date(qtbot, config):
    with (
        patch("analecta.updater.checker.get_current_version", return_value="0.2.0"),
        patch(
            "analecta.updater.checker.fetch_latest_version",
            new=AsyncMock(return_value="0.2.0"),
        ),
        patch("analecta.updater.checker.UpdateDialog") as mock_dlg,
    ):
        await check_and_notify(config)

    mock_dlg.assert_not_called()


@pytest.mark.asyncio
async def test_check_and_notify_no_dialog_on_network_error(qtbot, config):
    with (
        patch("analecta.updater.checker.get_current_version", return_value="0.1.0"),
        patch(
            "analecta.updater.checker.fetch_latest_version",
            new=AsyncMock(return_value=None),
        ),
        patch("analecta.updater.checker.UpdateDialog") as mock_dlg,
    ):
        await check_and_notify(config)

    mock_dlg.assert_not_called()


@pytest.mark.asyncio
async def test_check_and_notify_shows_dialog_when_update_available(qtbot, config):
    mock_instance = MagicMock()
    with (
        patch("analecta.updater.checker.get_current_version", return_value="0.1.0"),
        patch(
            "analecta.updater.checker.fetch_latest_version",
            new=AsyncMock(return_value="0.2.0"),
        ),
        patch(
            "analecta.updater.checker.UpdateDialog", return_value=mock_instance
        ) as mock_dlg,
    ):
        await check_and_notify(config)

    mock_dlg.assert_called_once_with("0.1.0", "0.2.0", None)
    mock_instance.exec.assert_called_once()
