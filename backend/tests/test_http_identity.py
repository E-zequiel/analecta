import re

from analecta.extraction.http_identity import build_headers


def test_document_headers_shape():
    headers = build_headers("document")
    assert headers["Sec-Fetch-Dest"] == "document"
    assert headers["Sec-Fetch-Mode"] == "navigate"
    assert headers["Sec-Fetch-Site"] == "none"
    assert headers["Sec-Fetch-User"] == "?1"
    assert headers["Upgrade-Insecure-Requests"] == "1"
    assert "text/html" in headers["Accept"]


def test_image_headers_shape():
    headers = build_headers("image")
    assert headers["Sec-Fetch-Dest"] == "image"
    assert headers["Sec-Fetch-Mode"] == "no-cors"
    assert headers["Sec-Fetch-Site"] == "cross-site"
    assert "Sec-Fetch-User" not in headers
    assert "image/" in headers["Accept"]


def test_api_headers_shape():
    headers = build_headers("api")
    assert headers["Sec-Fetch-Dest"] == "empty"
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Sec-Fetch-Site"] == "cross-site"
    assert headers["Accept"] == "*/*"


def test_user_agent_has_no_identifying_strings():
    ua = build_headers("document")["User-Agent"]
    for leak in ("Analecta", "analecta", "Electron", "github", "E-zequiel"):
        assert leak not in ua
    assert ua.startswith("Mozilla/5.0 (X11; Linux x86_64)")
    assert "Chrome/" in ua
    assert "Safari/537.36" in ua


def test_user_agent_minor_build_patch_frozen():
    ua = build_headers("document")["User-Agent"]
    assert re.search(r"Chrome/\d+\.0\.0\.0 Safari", ua)


def test_chrome_major_derived_from_env(monkeypatch):
    monkeypatch.setenv("ANALECTA_CHROME_MAJOR", "128")
    headers = build_headers("document")
    assert "Chrome/128.0.0.0" in headers["User-Agent"]
    assert '"Chromium";v="128"' in headers["Sec-CH-UA"]
    assert '"Google Chrome";v="128"' in headers["Sec-CH-UA"]


def test_chrome_major_falls_back_without_electron_parent(monkeypatch):
    monkeypatch.delenv("ANALECTA_CHROME_MAJOR", raising=False)
    headers = build_headers("document")
    assert "Chrome/" in headers["User-Agent"]
    assert headers["Sec-CH-UA"]


def test_sec_ch_ua_matches_user_agent_major(monkeypatch):
    monkeypatch.setenv("ANALECTA_CHROME_MAJOR", "140")
    headers = build_headers("api")
    assert "Chrome/140.0.0.0" in headers["User-Agent"]
    assert 'v="140"' in headers["Sec-CH-UA"]


def test_accept_language_is_generic_not_real_locale():
    headers = build_headers("document")
    assert headers["Accept-Language"] == "en-US,en;q=0.9"


def test_does_not_set_accept_encoding():
    for purpose in ("document", "image", "api"):
        assert "Accept-Encoding" not in build_headers(purpose)
