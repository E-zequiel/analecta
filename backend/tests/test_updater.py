"""Tests for scripts/deps_update.py pure utility functions."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
import deps_update  # pyright: ignore[reportMissingImports]
from deps_update import (  # pyright: ignore[reportMissingImports]
    _WORKSPACE_DIR,
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


class TestWorkspaceDirCoverage:
    def test_covers_every_pnpm_workspace_project(self) -> None:
        """Every project pnpm's workspace resolves to must have a _WORKSPACE_DIR entry.

        Guards against the failure mode this suite is named after: a new
        workspace (or the root package) added to the monorepo without a
        matching entry here would have its dependencies age silently —
        `update_node()` is only ever called for names in this dict, so a
        missed workspace is never even checked against `pnpm outdated`.
        """
        repo_root = Path(__file__).parents[2]
        workspace_config = yaml.safe_load(
            (repo_root / "pnpm-workspace.yaml").read_text()
        )
        project_dirs = [repo_root] + [
            repo_root / rel_dir for rel_dir in workspace_config["packages"]
        ]
        for d in project_dirs:
            assert d.is_dir(), (
                f"{d} is not a directory — pnpm-workspace.yaml's packages: entries"
                " are assumed to be literal dirs, not globs"
            )
        actual_names = {
            json.loads((d / "package.json").read_text())["name"] for d in project_dirs
        }
        assert actual_names == set(_WORKSPACE_DIR)

    def test_main_calls_update_node_for_every_workspace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() must actually process every workspace listed in _WORKSPACE_DIR.

        The sibling test above only guards that _WORKSPACE_DIR's keys match
        pnpm's real workspace projects — it says nothing about whether main()
        itself calls update_node() for each one. main() wires those calls as
        hardcoded literals, not a loop derived from _WORKSPACE_DIR, so a future
        workspace could be added to the dict (passing the sibling test) while
        main() never picks it up.
        """
        called_workspaces: list[str] = []

        def fake_update_node(
            cooldown: int, workspace: str
        ) -> tuple[list[Any], list[Any], bool]:
            called_workspaces.append(workspace)
            return [], [], False

        def fake_update_python(cooldown: int) -> tuple[list[Any], list[Any], bool]:
            return [], [], False

        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(sys, "argv", ["deps_update.py"])

        deps_update.main()

        assert set(called_workspaces) == set(_WORKSPACE_DIR)
