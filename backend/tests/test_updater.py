"""Tests for scripts/deps_update.py pure utility functions."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from deps_update import (  # pyright: ignore[reportMissingImports]
    _age_ok,
    _parse_iso,
    _parse_pnpm_outdated,
)


class TestParseIso:
    def test_z_suffix_returns_utc_aware(self) -> None:
        dt = _parse_iso("2025-01-15T10:00:00Z")
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_microseconds_with_z(self) -> None:
        dt = _parse_iso("2025-01-15T10:00:00.123456Z")
        assert dt.tzinfo is not None
        assert dt.microsecond == 123456

    def test_offset_returns_aware(self) -> None:
        dt = _parse_iso("2025-01-15T10:00:00+00:00")
        assert dt.tzinfo is not None

    def test_naive_string_assumed_utc(self) -> None:
        """PyPI upload_time field has no timezone info — must be treated as UTC."""
        dt = _parse_iso("2025-01-15T10:00:00")
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            _parse_iso("not-a-date")


class TestAgeOk:
    def test_old_package_passes_gate(self) -> None:
        old = datetime.now(UTC) - timedelta(days=10)
        assert _age_ok(old, cooldown=3) is True

    def test_recent_package_blocked(self) -> None:
        new = datetime.now(UTC) - timedelta(hours=1)
        assert _age_ok(new, cooldown=3) is False

    def test_exactly_cooldown_days_passes(self) -> None:
        boundary = datetime.now(UTC) - timedelta(days=3)
        assert _age_ok(boundary, cooldown=3) is True


class TestParsePnpmOutdated:
    def test_flat_format(self) -> None:
        raw = json.dumps(
            {"svelte": {"current": "5.0.0", "wanted": "5.1.0", "latest": "5.1.0"}}
        )
        result = _parse_pnpm_outdated(raw)
        assert "svelte" in result
        assert result["svelte"]["wanted"] == "5.1.0"

    def test_workspace_nested_format(self) -> None:
        raw = json.dumps(
            {
                "frontend": {
                    "vite": {"current": "6.0.0", "wanted": "6.1.0", "latest": "6.1.0"}
                }
            }
        )
        result = _parse_pnpm_outdated(raw)
        assert "vite" in result

    def test_empty_returns_empty(self) -> None:
        assert _parse_pnpm_outdated(json.dumps({})) == {}
