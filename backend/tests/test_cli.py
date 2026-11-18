"""T6: the sidecar binary's CLI subcommands (extract / index / log).

Same binary, same schema (D3). Default invocation (no args) stays the
sidecar server; subcommands run through the CLI. Two connection modes:
attached (reads the 0600 discovery file, talks loopback HTTP with the
session token) and standalone (opens the vault directly under flock).
Output: human text by default, ``--json`` for bots (S3/S8).
"""

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from analecta.cli import (
    build_index_output,
    build_log_output,
    discovery_request_token,
)
from analecta.extraction.core import ExtractedContent
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.storage.vault import VaultManager


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()


def _entry(**kwargs: Any) -> EntryRecord:
    defaults: dict[str, Any] = dict(
        title="Test Entry",
        url="https://example.com/test",
        file_path="/vault/pages/2026-04-30-test-entry.md",
        source_type="article",
        created_at="2026-04-30T10:00:00+00:00",
        updated_at="2026-04-30T10:00:00+00:00",
    )
    defaults.update(kwargs)
    return EntryRecord(**defaults)


# ---------------------------------------------------------------------------
# Discovery request assembly (attached mode)
# ---------------------------------------------------------------------------


def test_discovery_request_token_reads_file(tmp_path: Path) -> None:
    from analecta.session_security import SESSION_FILE_NAME, SessionDiscovery

    SessionDiscovery(tmp_path).write(port=4242, token="tok", pid=1)
    req = discovery_request_token(
        tmp_path / SESSION_FILE_NAME, path="/entries", body={"url": "https://x"}
    )
    assert req is not None
    url, headers = req
    assert url == "http://localhost:4242/api/v1/entries"
    assert headers["Authorization"] == "Bearer tok"


def test_discovery_request_token_missing_file_returns_none(tmp_path: Path) -> None:
    assert discovery_request_token(tmp_path / "session.json", path="/entries") is None


# ---------------------------------------------------------------------------
# Index output (OKF-style)
# ---------------------------------------------------------------------------


@pytest.fixture
def populated(
    tmp_path: Path,
) -> Generator[tuple[VaultIndex, VaultManager]]:
    index = VaultIndex(tmp_path / "vault.db")
    vault = VaultManager(tmp_path / "vault")
    vault.ensure_dirs()
    index.add_entry(_entry(title="Article A", url="https://a.com"))
    index.add_entry(
        _entry(
            title="Video B",
            url="https://b.com",
            source_type="youtube",
            created_at="2026-05-01T10:00:00+00:00",
            updated_at="2026-05-01T10:00:00+00:00",
        )
    )
    yield index, vault
    index.close()


def test_build_index_output_groups_captures_and_synthesis(
    populated: tuple[VaultIndex, VaultManager],
) -> None:
    index, vault = populated
    from analecta.storage.synthesis import create_synthesis_entry

    capture = index.list_entries()[0]
    create_synthesis_entry(
        index,
        vault,
        title="Synth",
        body="body",
        source_entries=[capture],
        generated_by="bot/x",
    )
    text = build_index_output(index)
    assert "# Library" in text
    assert "# Synthesis" in text
    assert "[Article A]" in text
    assert "[Synth]" in text
    # Captures are not listed under Synthesis.
    synth_section = text.split("# Synthesis")[1]
    assert "Article A" not in synth_section


def test_build_index_output_json(populated: tuple[VaultIndex, VaultManager]) -> None:
    index, _ = populated
    data = json.loads(build_index_output(index, as_json=True))
    assert isinstance(data, list)
    assert {d["title"] for d in data} >= {"Article A", "Video B"}
    assert all("read_state" in d and "path" in d and "type" in d for d in data)


# ---------------------------------------------------------------------------
# Log output
# ---------------------------------------------------------------------------


def test_build_log_output_groups_by_date(
    populated: tuple[VaultIndex, VaultManager],
) -> None:
    index, _ = populated
    text = build_log_output(index)
    # Newest first; ISO date headings per OKF §9.
    assert "## 2026-05-01" in text
    assert "## 2026-04-30" in text
    assert text.index("## 2026-05-01") < text.index("## 2026-04-30")
    assert "**Creation**" in text


def test_build_log_output_json(populated: tuple[VaultIndex, VaultManager]) -> None:
    index, _ = populated
    data = json.loads(build_log_output(index, as_json=True))
    assert data[0]["date"] >= data[-1]["date"]
    assert all("items" in group for group in data)


# ---------------------------------------------------------------------------
# Standalone extract (flock; extraction monkeypatched — no network)
# ---------------------------------------------------------------------------


def test_standalone_extract_writes_vault(tmp_path: Path, monkeypatch) -> None:
    from analecta.cli import run_extract_standalone

    fake = ExtractedContent(
        title="CLI Capture",
        html="<p>body</p>",
        url="https://example.com/cli",
        source_type="article",
        metadata={},
    )

    async def fake_extract(url: str) -> ExtractedContent:
        return fake

    monkeypatch.setattr("analecta.cli.extract", fake_extract)

    result = run_extract_standalone(tmp_path / "vault", "https://example.com/cli")
    assert result is not None
    assert result.title == "CLI Capture"
    page = Path(result.file_path)
    assert page.exists()
    assert "type: reference" in page.read_text(encoding="utf-8")


def test_standalone_extract_fails_when_locked(tmp_path: Path, monkeypatch) -> None:
    from analecta.cli import run_extract_standalone
    from analecta.session_security import VaultLock

    holder = VaultLock(tmp_path / "vault").acquire()
    assert holder is not None
    try:

        async def locked_extract(url: str) -> ExtractedContent:
            pytest.fail("extraction must not run when locked")

        monkeypatch.setattr("analecta.cli.extract", locked_extract)
        with pytest.raises(RuntimeError, match="locked"):
            run_extract_standalone(tmp_path / "vault", "https://example.com/cli")
    finally:
        VaultLock(tmp_path / "vault").release(holder)
