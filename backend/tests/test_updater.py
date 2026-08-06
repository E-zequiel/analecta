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
    _pr_body,
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
        """Every project pnpm's workspace resolves to must have a _WORKSPACE_DIR entry,
        and each entry's directory must actually be that project (not another one).

        Guards against the failure mode this suite is named after: a new
        workspace (or the root package) added to the monorepo without a
        matching entry here would have its dependencies age silently —
        `update_node()` is only ever called for names in this dict, so a
        missed workspace is never even checked against `pnpm outdated`. The
        key->value check guards a narrower variant: a swapped or mistyped
        directory would still pass a keys-only comparison while writing
        version bumps into the wrong package.json.
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

        for name, rel_dir in _WORKSPACE_DIR.items():
            pkg_path = repo_root / rel_dir / "package.json"
            actual_name = json.loads(pkg_path.read_text())["name"]
            assert actual_name == name, (
                f"_WORKSPACE_DIR[{name!r}] = {rel_dir!r} points at a package.json"
                f" named {actual_name!r}, not {name!r}"
            )


class TestMainPartialWorkspaceFailure:
    """main() must not let a hard error in one workspace/ecosystem block a PR
    for the others — only a run that produces zero successful updates anywhere
    should exit non-zero.
    """

    def test_one_workspace_error_does_not_abort_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release_dt = datetime.now(UTC) - timedelta(days=30)

        def fake_update_node(
            cooldown: int, workspace: str
        ) -> tuple[list[Any], list[Any], list[str]]:
            if workspace == "analecta":
                return [], [], ["All npm registry fetches failed"]
            return [("svelte", "5.0.0", "5.1.0", release_dt)], [], []

        def fake_update_python(cooldown: int) -> tuple[list[Any], list[Any], list[str]]:
            return [], [], []

        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(sys, "argv", ["deps_update.py"])

        deps_update.main()  # must return normally, not sys.exit(1)

    def test_total_failure_still_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If nothing succeeded anywhere and something errored, fail loudly."""

        def fake_update_node(
            cooldown: int, workspace: str
        ) -> tuple[list[Any], list[Any], list[str]]:
            return [], [], ["All npm registry fetches failed"]

        def fake_update_python(cooldown: int) -> tuple[list[Any], list[Any], list[str]]:
            return [], [], []

        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(sys, "argv", ["deps_update.py"])

        with pytest.raises(SystemExit) as exc_info:
            deps_update.main()
        assert exc_info.value.code == 1


class TestPrBody:
    def test_reports_workspace_column_and_errors(self) -> None:
        """The Node section must tag each row with its workspace and surface
        per-workspace errors so a reviewer sees them without checking Action
        logs — the PR is opened with the default GITHUB_TOKEN, which never
        triggers ci.yml, so the PR body is the only review surface.
        """
        release_dt = datetime.now(UTC) - timedelta(days=30)
        body = _pr_body(
            py_up=[],
            py_sk=[],
            py_blocked=[],
            py_errors=[],
            nd_up=[("svelte", "5.0.0", "5.1.0", release_dt)],
            nd_sk=[],
            nd_blocked=[],
            nd_errors=["`analecta`: All npm registry fetches failed"],
            nd_ws=["frontend"],
            cooldown=10,
        )
        assert "| `svelte` | `frontend` |" in body
        assert "`analecta`: All npm registry fetches failed" in body
