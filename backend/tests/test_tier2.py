from typing import Any

import pytest

from analecta.extraction.core import ExtractionError
from analecta.extraction.tier2 import render_url


def _mock_httpx(mocker, json_body: dict[str, Any]) -> None:
    mock_resp = mocker.MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = json_body

    mock_client = mocker.MagicMock()
    mock_client.__aenter__ = mocker.AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = mocker.AsyncMock(return_value=False)
    mock_client.post = mocker.AsyncMock(return_value=mock_resp)
    mocker.patch(
        "analecta.extraction.tier2.httpx2.AsyncClient", return_value=mock_client
    )


@pytest.mark.asyncio
async def test_render_url_raises_when_port_not_set(monkeypatch):
    monkeypatch.delenv("ANALECTA_RENDER_PORT", raising=False)
    import analecta.extraction.tier2 as t2

    original = t2._PORT
    t2._PORT = 0
    try:
        with pytest.raises(ExtractionError, match="ANALECTA_RENDER_PORT"):
            await render_url("https://example.com")
    finally:
        t2._PORT = original


@pytest.mark.asyncio
async def test_render_url_ok_maps_all_fields(monkeypatch, mocker):
    monkeypatch.setenv("ANALECTA_RENDER_PORT", "9999")
    monkeypatch.setenv("ANALECTA_RENDER_TOKEN", "tok")
    _mock_httpx(
        mocker,
        {
            "ok": True,
            "content": "<p>Body</p>",
            "title": "Title",
            "author": "Alice",
            "description": "Desc",
            "published": "2024-01-01",
            "final_url": "https://example.com/redirected",
        },
    )
    result = await render_url("https://example.com/article")
    assert result.ok is True
    assert result.content == "<p>Body</p>"
    assert result.title == "Title"
    assert result.author == "Alice"
    assert result.description == "Desc"
    assert result.published == "2024-01-01"
    assert result.outer_html is None
    assert result.final_url == "https://example.com/redirected"


@pytest.mark.asyncio
async def test_render_url_fallback_outer_html(monkeypatch, mocker):
    monkeypatch.setenv("ANALECTA_RENDER_PORT", "9999")
    monkeypatch.setenv("ANALECTA_RENDER_TOKEN", "tok")
    _mock_httpx(mocker, {"ok": False, "outer_html": "<html>fallback</html>"})
    result = await render_url("https://example.com/spa")
    assert result.ok is False
    assert result.outer_html == "<html>fallback</html>"
    assert result.content is None
    assert result.final_url is None


@pytest.mark.asyncio
async def test_render_url_non_string_values_become_none(monkeypatch, mocker):
    monkeypatch.setenv("ANALECTA_RENDER_PORT", "9999")
    monkeypatch.setenv("ANALECTA_RENDER_TOKEN", "tok")
    # Numeric/null values for string fields must not propagate as-is.
    _mock_httpx(mocker, {"ok": True, "content": 42, "title": None})
    result = await render_url("https://example.com/bad")
    assert result.content is None
    assert result.title is None
