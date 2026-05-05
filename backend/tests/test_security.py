import pytest

from analecta.config import AppConfig
from analecta.security.virustotal import (
    ScanResult,
    VirusTotalKeyError,
    VirusTotalRateLimitError,
    VirusTotalScanner,
    VirusTotalTimeoutError,
    is_available,
)

_ID = "u-abc123def456"
_CLEAN_STATS = {"malicious": 0, "suspicious": 0, "undetected": 70, "harmless": 10}
_MALICIOUS_STATS = {"malicious": 5, "suspicious": 2, "undetected": 60, "harmless": 3}

_VT_MODULE = "analecta.security.virustotal"


def _resp(mocker, status_code=200, json_body=None):
    r = mocker.Mock()
    r.status_code = status_code
    r.raise_for_status = mocker.Mock(
        side_effect=None if status_code < 400 else Exception(f"HTTP {status_code}")
    )
    r.json = mocker.Mock(return_value=json_body or {})
    return r


def _mock_async_client(mocker):
    client = mocker.MagicMock()
    client.__aenter__ = mocker.AsyncMock(return_value=client)
    client.__aexit__ = mocker.AsyncMock(return_value=False)
    return client


def _scanner(**kwargs):
    return VirusTotalScanner(poll_interval=0.0, **kwargs)


# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------


def test_scan_result_total():
    r = ScanResult(
        "https://x.com", _ID, malicious=1, suspicious=2, undetected=5, harmless=3
    )
    assert r.total == 11


def test_scan_result_verdict_malicious():
    r = ScanResult("u", _ID, malicious=1, suspicious=0, undetected=0, harmless=0)
    assert r.verdict == "malicious"


def test_scan_result_verdict_suspicious():
    r = ScanResult("u", _ID, malicious=0, suspicious=1, undetected=0, harmless=0)
    assert r.verdict == "suspicious"


def test_scan_result_verdict_clean():
    r = ScanResult("u", _ID, malicious=0, suspicious=0, undetected=5, harmless=5)
    assert r.verdict == "clean"


# ---------------------------------------------------------------------------
# _get_api_key
# ---------------------------------------------------------------------------


def test_get_api_key_raises_when_missing(mocker):
    mocker.patch("keyring.get_password", return_value=None)
    with pytest.raises(VirusTotalKeyError):
        _scanner()._get_api_key()


def test_get_api_key_returns_key(mocker):
    mocker.patch("keyring.get_password", return_value="my-secret-key")
    assert _scanner()._get_api_key() == "my-secret-key"


# ---------------------------------------------------------------------------
# _submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_returns_analysis_id(mocker):
    resp = _resp(mocker, json_body={"data": {"id": _ID}})
    client = mocker.AsyncMock()
    client.post = mocker.AsyncMock(return_value=resp)

    result = await _scanner()._submit("https://example.com", client)
    assert result == _ID


@pytest.mark.asyncio
async def test_submit_raises_on_rate_limit(mocker):
    resp = _resp(mocker, status_code=429)
    client = mocker.AsyncMock()
    client.post = mocker.AsyncMock(return_value=resp)

    with pytest.raises(VirusTotalRateLimitError):
        await _scanner()._submit("https://example.com", client)


# ---------------------------------------------------------------------------
# _poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_returns_attrs_when_completed(mocker):
    attrs = {"status": "completed", "stats": _CLEAN_STATS}
    resp = _resp(mocker, json_body={"data": {"attributes": attrs}})
    client = mocker.AsyncMock()
    client.get = mocker.AsyncMock(return_value=resp)

    result = await _scanner()._poll(_ID, client)
    assert result == attrs


@pytest.mark.asyncio
async def test_poll_raises_on_timeout(mocker):
    attrs = {"status": "in-progress", "stats": {}}
    resp = _resp(mocker, json_body={"data": {"attributes": attrs}})
    client = mocker.AsyncMock()
    client.get = mocker.AsyncMock(return_value=resp)

    with pytest.raises(VirusTotalTimeoutError):
        await _scanner(max_polls=1)._poll(_ID, client)


@pytest.mark.asyncio
async def test_poll_raises_on_rate_limit(mocker):
    resp = _resp(mocker, status_code=429)
    client = mocker.AsyncMock()
    client.get = mocker.AsyncMock(return_value=resp)

    with pytest.raises(VirusTotalRateLimitError):
        await _scanner()._poll(_ID, client)


@pytest.mark.asyncio
async def test_poll_retries_until_completed(mocker):
    in_progress_attrs = {"status": "in-progress", "stats": {}}
    in_progress = _resp(
        mocker, json_body={"data": {"attributes": in_progress_attrs}}
    )
    completed_attrs = {"status": "completed", "stats": _CLEAN_STATS}
    completed = _resp(mocker, json_body={"data": {"attributes": completed_attrs}})

    client = mocker.AsyncMock()
    client.get = mocker.AsyncMock(
        side_effect=[in_progress, in_progress, completed]
    )

    result = await _scanner(max_polls=5)._poll(_ID, client)
    assert result["status"] == "completed"
    assert client.get.call_count == 3


# ---------------------------------------------------------------------------
# scan (end-to-end with mocked internals)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_returns_clean_result(mocker):
    mocker.patch("keyring.get_password", return_value="fake-key")
    mocker.patch.object(VirusTotalScanner, "_submit", return_value=_ID)
    mocker.patch.object(
        VirusTotalScanner,
        "_poll",
        return_value={"status": "completed", "stats": _CLEAN_STATS},
    )
    mock_client = _mock_async_client(mocker)
    mocker.patch(f"{_VT_MODULE}.httpx.AsyncClient", return_value=mock_client)

    result = await _scanner().scan("https://example.com")

    assert result.verdict == "clean"
    assert result.malicious == 0
    assert result.url == "https://example.com"
    assert result.analysis_id == _ID


@pytest.mark.asyncio
async def test_scan_returns_malicious_result(mocker):
    mocker.patch("keyring.get_password", return_value="fake-key")
    mocker.patch.object(VirusTotalScanner, "_submit", return_value=_ID)
    mocker.patch.object(
        VirusTotalScanner,
        "_poll",
        return_value={"status": "completed", "stats": _MALICIOUS_STATS},
    )
    mock_client = _mock_async_client(mocker)
    mocker.patch(f"{_VT_MODULE}.httpx.AsyncClient", return_value=mock_client)

    result = await _scanner().scan("https://evil.example.com")

    assert result.verdict == "malicious"
    assert result.malicious == 5
    assert result.suspicious == 2


@pytest.mark.asyncio
async def test_scan_propagates_key_error(mocker):
    mocker.patch("keyring.get_password", return_value=None)
    with pytest.raises(VirusTotalKeyError):
        await _scanner().scan("https://example.com")


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


def test_is_available_false_when_disabled(mocker):
    mocker.patch("keyring.get_password", return_value="key")
    assert is_available(AppConfig(virustotal_enabled=False)) is False


def test_is_available_false_when_no_key(mocker):
    mocker.patch("keyring.get_password", return_value=None)
    assert is_available(AppConfig(virustotal_enabled=True)) is False


def test_is_available_true_when_enabled_and_key_present(mocker):
    mocker.patch("keyring.get_password", return_value="my-key")
    assert is_available(AppConfig(virustotal_enabled=True)) is True
