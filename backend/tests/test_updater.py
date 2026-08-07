"""Tests for scripts/deps_update.py pure utility functions."""

import json
import subprocess
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
    _sanitize_reason,
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
            cooldown: int, workspace: str, registry_cache: dict[str, Any] | None = None
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
            cooldown: int, workspace: str, registry_cache: dict[str, Any] | None = None
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
        nd_error_msg = "All npm registry fetches failed"
        body = _pr_body(
            py_up=[],
            py_sk=[],
            py_blocked=[],
            py_errors=[],
            nd_up=[("svelte", "5.0.0", "5.1.0", release_dt)],
            nd_sk=[],
            nd_blocked=[],
            nd_errors=[nd_error_msg],
            nd_error_ws=["analecta"],
            nd_ws=["frontend"],
            cooldown=10,
        )
        assert "| `svelte` | `frontend` |" in body
        # The workspace name is trusted (code-controlled, from
        # _WORKSPACE_DIR) — its backticks are applied after sanitizing only
        # the free-form message, so they survive into the PR body intact.
        assert f"`analecta`: {nd_error_msg}" in body

    def test_errors_are_sanitized_before_joining(self) -> None:
        """Unlike `blocked`, `errors` carries raw subprocess stderr straight
        through to the committed PR body — a stray backtick or embedded
        newline (routine for a multi-line uv/pnpm failure) must not be able
        to break out of its single-line context.
        """
        raw = "evil-pkg: uv lock failed — `oops` | broke\nthings"
        body = _pr_body(
            py_up=[],
            py_sk=[],
            py_blocked=[],
            py_errors=[raw],
            nd_up=[],
            nd_sk=[],
            nd_blocked=[],
            nd_errors=[],
            nd_error_ws=[],
            nd_ws=[],
            cooldown=10,
        )
        assert raw not in body
        assert _sanitize_reason(raw) in body

    def test_node_error_message_is_sanitized_even_with_workspace(self) -> None:
        """The workspace-name backticks are trusted, but the message next to
        them is still attacker-reachable subprocess output and must still go
        through _sanitize_reason.
        """
        raw = "uv lock failed — `oops` | broke\nthings"
        body = _pr_body(
            py_up=[],
            py_sk=[],
            py_blocked=[],
            py_errors=[],
            nd_up=[],
            nd_sk=[],
            nd_blocked=[],
            nd_errors=[raw],
            nd_error_ws=["frontend"],
            nd_ws=[],
            cooldown=10,
        )
        assert raw not in body
        assert f"`frontend`: {_sanitize_reason(raw)}" in body


class TestVerifyGateIncludesErroredBatch:
    """A package failing in one Python update must not skip verification for
    the packages that DID apply — see scripts/deps_update.py's earlier bug
    where `and not py_errors` silently skipped `_verify_python` whenever any
    package errored, letting unverified bumps ship in the PR.
    """

    def test_verify_python_runs_despite_sibling_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release_dt = datetime.now(UTC) - timedelta(days=30)
        applied = [("svelte-check", "1.0.0", "1.1.0", release_dt)]

        def fake_update_python(
            cooldown: int,
        ) -> tuple[list[Any], list[Any], list[str]]:
            return applied, [], ["other-pkg: uv lock failed — boom"]

        def fake_update_node(
            cooldown: int, workspace: str, registry_cache: dict[str, Any] | None = None
        ) -> tuple[list[Any], list[Any], list[str]]:
            return [], [], []

        verify_calls: list[list[Any]] = []

        def fake_verify_python(
            backend: Path, uv_lock_path: Path, snap: bytes, batch: list[Any]
        ) -> tuple[list[Any], list[Any]]:
            verify_calls.append(batch)
            return batch, []

        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "_verify_python", fake_verify_python)
        monkeypatch.setattr(sys, "argv", ["deps_update.py", "--verify"])

        deps_update.main()

        assert verify_calls == [applied]


class TestApplyNodePackageExceptionSafety:
    """A write failure between `pnpm add` and the lockfile resync must not
    leave package.json/pnpm-lock.yaml mutually inconsistent — see
    scripts/deps_update.py's earlier bug where only the checked
    returncode != 0 branches called the restore closure, leaving an
    unexpected exception free to propagate past the snapshot and crash the
    whole run, discarding every other workspace's already-applied updates.
    """

    def test_restores_files_on_unexpected_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pkg_dir = tmp_path / "frontend"
        pkg_dir.mkdir()
        pkg_path = pkg_dir / "package.json"
        lock_path = tmp_path / "pnpm-lock.yaml"
        original_pkg = '{"dependencies": {"svelte": "5.0.0"}}'
        original_lock = "lockfileVersion: 9\n"
        pkg_path.write_text(original_pkg)
        lock_path.write_text(original_lock)

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "_WORKSPACE_DIR", {"frontend": "frontend"})

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fake_ensure_exact_specifier(
            workspace_dir: str, name: str, version: str
        ) -> bool:
            raise OSError("disk full")

        monkeypatch.setattr(deps_update, "_run", fake_run)
        monkeypatch.setattr(
            deps_update, "_ensure_exact_specifier", fake_ensure_exact_specifier
        )

        ok, reason = deps_update._apply_node_package("frontend", "svelte", "5.1.0")

        assert ok is False
        assert "disk full" in reason
        assert pkg_path.read_text() == original_pkg
        assert lock_path.read_text() == original_lock

    def test_restores_files_on_unexpected_non_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A prior version of this except clause only caught OSError — any
        other exception type (e.g. from subprocess.run's text-mode decoding,
        or a bug in a called helper) propagated straight past the finally's
        restore and out of _apply_node_package uncaught.
        """
        pkg_dir = tmp_path / "frontend"
        pkg_dir.mkdir()
        pkg_path = pkg_dir / "package.json"
        lock_path = tmp_path / "pnpm-lock.yaml"
        original_pkg = '{"dependencies": {"svelte": "5.0.0"}}'
        original_lock = "lockfileVersion: 9\n"
        pkg_path.write_text(original_pkg)
        lock_path.write_text(original_lock)

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "_WORKSPACE_DIR", {"frontend": "frontend"})

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fake_ensure_exact_specifier(
            workspace_dir: str, name: str, version: str
        ) -> bool:
            raise ValueError("unexpected shape")

        monkeypatch.setattr(deps_update, "_run", fake_run)
        monkeypatch.setattr(
            deps_update, "_ensure_exact_specifier", fake_ensure_exact_specifier
        )

        ok, reason = deps_update._apply_node_package("frontend", "svelte", "5.1.0")

        assert ok is False
        assert "unexpected shape" in reason
        assert pkg_path.read_text() == original_pkg
        assert lock_path.read_text() == original_lock


class TestVerifyNodeResyncsNodeModules:
    """Restoring package.json/pnpm-lock.yaml bytes during bisection must be
    followed by a node_modules resync — check.sh resolves its tools
    (eslint, prettier, tsc) from the installed tree, not the manifests, so a
    stale node_modules can misattribute a block to an innocent package.
    """

    def test_resync_runs_after_every_restore(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pkg_path = tmp_path / "package.json"
        lock_path = tmp_path / "pnpm-lock.yaml"
        pkg_bytes = b"{}"
        lock_bytes = b"lockfileVersion: 9\n"
        pkg_path.write_bytes(pkg_bytes)
        lock_path.write_bytes(lock_bytes)
        snapshots = {pkg_path: pkg_bytes, lock_path: lock_bytes}

        release_dt = datetime.now(UTC) - timedelta(days=30)
        batch = [("frontend", ("svelte", "5.0.0", "5.1.0", release_dt))]

        resync_calls = 0

        def fake_resync() -> bool:
            nonlocal resync_calls
            resync_calls += 1
            return True

        # Full-batch check.sh fails, then the one per-step check.sh also fails.
        check_results = iter([False, False])

        def fake_run_check(repo_root: Path, scope: str) -> bool:
            return next(check_results)

        def fake_apply(workspace: str, name: str, version: str) -> tuple[bool, str]:
            return True, ""

        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)

        survivors, blocked, errors = deps_update._verify_node(snapshots, batch)

        assert survivors == []
        assert len(blocked) == 1
        assert errors == []
        assert resync_calls == 2  # full-batch restore + the one blocked step


class TestVerifyNodeResyncFailure:
    """A failed `_resync_node_modules()` mid-bisection means node_modules no
    longer reliably reflects the manifests on disk — every check.sh result
    after that point would be measuring a broken environment, not the
    package under test. See scripts/deps_update.py's docstring on
    `_verify_node` for the two failure points this covers.
    """

    def _snapshots(self, tmp_path: Path) -> dict[Path, bytes]:
        pkg_path = tmp_path / "package.json"
        lock_path = tmp_path / "pnpm-lock.yaml"
        pkg_bytes = b"{}"
        lock_bytes = b"lockfileVersion: 9\n"
        pkg_path.write_bytes(pkg_bytes)
        lock_path.write_bytes(lock_bytes)
        return {pkg_path: pkg_bytes, lock_path: lock_bytes}

    def test_initial_resync_failure_discards_the_whole_batch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-batch resync (restoring the state before any package in
        the batch was applied) failing means bisection can't even start on
        a trustworthy tree — the whole batch must be dropped with an error,
        not silently bisected against a stale node_modules.
        """
        snapshots = self._snapshots(tmp_path)
        release_dt = datetime.now(UTC) - timedelta(days=30)
        batch = [("frontend", ("svelte", "5.0.0", "5.1.0", release_dt))]

        apply_calls = 0

        def fake_apply(workspace: str, name: str, version: str) -> tuple[bool, str]:
            nonlocal apply_calls
            apply_calls += 1
            return True, ""

        def fake_run_check(repo_root: Path, scope: str) -> bool:
            return False

        def fake_resync() -> bool:
            return False

        monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)

        survivors, blocked, errors = deps_update._verify_node(snapshots, batch)

        assert survivors == []
        assert blocked == []
        assert len(errors) == 1
        assert "node_modules resync failed" in errors[0]
        assert apply_calls == 0  # bisection never started

    def test_per_step_resync_failure_keeps_current_verdict_but_stops(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The package whose isolation check just ran gets recorded (that
        verdict was measured against a valid, fully-applied tree before the
        restore) — but once the resync that prepares the *next* candidate's
        clean baseline fails, remaining packages must not be bisected
        against an unreliable environment.
        """
        snapshots = self._snapshots(tmp_path)
        release_dt = datetime.now(UTC) - timedelta(days=30)
        batch = [
            ("frontend", ("svelte", "5.0.0", "5.1.0", release_dt)),
            ("frontend", ("vite", "6.0.0", "6.1.0", release_dt)),
        ]

        # Full-batch check.sh fails; first package's isolation check.sh also
        # fails (so it gets blocked); the second package is never reached.
        check_results = iter([False, False])

        def fake_run_check(repo_root: Path, scope: str) -> bool:
            return next(check_results)

        def fake_apply(workspace: str, name: str, version: str) -> tuple[bool, str]:
            return True, ""

        # First call is the initial full-batch restore (succeeds); second
        # call is the per-step resync after isolating "svelte" (fails).
        resync_results = iter([True, False])

        def fake_resync() -> bool:
            return next(resync_results)

        monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)

        survivors, blocked, errors = deps_update._verify_node(snapshots, batch)

        assert survivors == []
        assert [name for name, _, _ in blocked] == ["svelte"]
        assert len(errors) == 1
        assert "node_modules resync failed" in errors[0]
        assert "1 package(s) dropped from this batch" in errors[0]


class TestPypiReleaseDateFetchTracking:
    """Mirrors TestNpmReleaseDateFetchTracking below for the Python/uv side —
    fixing this diagnostic gap in one ecosystem but not the other would
    leave the exact same silent-degradation risk on whichever side got
    skipped.
    """

    def _backend(self, tmp_path: Path, dep_version: str = "0.1.0") -> Path:
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text(
            f'[project]\ndependencies = ["ruff=={dep_version}"]\n'
        )
        (backend / "uv.lock").write_text(
            f'[[package]]\nname = "ruff"\nversion = "{dep_version}"\n'
        )
        return backend

    def test_missing_version_timestamp_is_not_a_fetch_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._backend(tmp_path)
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)

        def fake_fetch_json(url: str, *, headers: Any = None) -> dict[str, Any] | None:
            # Reachable, valid JSON, but no upload_time for the target version.
            return {"info": {"version": "0.2.0"}, "releases": {"0.2.0": []}}

        monkeypatch.setattr(deps_update, "_fetch_json", fake_fetch_json)

        updated, skipped, errors = deps_update.update_python(10)

        assert updated == []
        assert skipped == []
        assert "registry fetches failed" not in "".join(errors)
        assert len(errors) == 1
        assert "release-date lookups failed" in errors[0]

    def test_partial_missing_timestamps_does_not_escalate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = self._backend(tmp_path)
        (backend / "pyproject.toml").write_text(
            '[project]\ndependencies = ["ruff==0.1.0", "httpx2==0.1.0"]\n'
        )
        (backend / "uv.lock").write_text(
            '[[package]]\nname = "ruff"\nversion = "0.1.0"\n'
            '[[package]]\nname = "httpx2"\nversion = "0.1.0"\n'
        )
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()

        def fake_fetch_json(url: str, *, headers: Any = None) -> dict[str, Any] | None:
            if "ruff" in url:
                return {"info": {"version": "0.2.0"}, "releases": {"0.2.0": []}}
            return {
                "info": {"version": "0.2.0"},
                "releases": {"0.2.0": [{"upload_time_iso_8601": recent}]},
            }

        monkeypatch.setattr(deps_update, "_fetch_json", fake_fetch_json)

        _updated, skipped, errors = deps_update.update_python(10)

        assert errors == []
        assert [name for name, _, _ in skipped] == ["httpx2"]


class TestNpmReleaseDateFetchTracking:
    """fetch_ok must only count registry reachability, not whether the
    specific version has a `time` entry — a real npm data gap (version
    present, timestamp missing) must not render as a "registry
    unreachable"-flavored error, since the registry answered fine. But it
    also can't go completely unreported: if literally every candidate
    package in the workspace hits this gap, cooldown couldn't be evaluated
    for any of them, which is worth its own distinct, accurately-labeled
    error — see the candidates/date_unknown counters in update_node.
    """

    def test_missing_version_timestamp_is_not_a_fetch_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_registry_data(
            name: str, cache: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            return {"time": {}}  # reachable, but no entry for the target version

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            stdout = json.dumps({"svelte": {"current": "5.0.0", "latest": "5.1.0"}})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
        monkeypatch.setattr(deps_update, "_run", fake_run)

        updated, skipped, errors = deps_update.update_node(10, "frontend")

        assert updated == []
        assert skipped == []
        assert "registry fetches failed" not in "".join(errors)
        assert len(errors) == 1
        assert "release-date lookups failed" in errors[0]

    def test_partial_missing_timestamps_does_not_escalate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only a 100% failure rate across candidates is worth an error —
        one package's stale registry data alongside another's healthy data
        stays a per-package [warn], same as before this counter existed.
        """

        recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()

        def fake_registry_data(
            name: str, cache: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            if name == "svelte":
                return {"time": {}}  # missing
            return {"time": {"6.1.0": recent}}  # present, but too recent (cooldown)

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            stdout = json.dumps(
                {
                    "svelte": {"current": "5.0.0", "latest": "5.1.0"},
                    "vite": {"current": "6.0.0", "latest": "6.1.0"},
                }
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
        monkeypatch.setattr(deps_update, "_run", fake_run)

        _updated, skipped, errors = deps_update.update_node(10, "frontend")

        assert errors == []
        assert [name for name, _, _ in skipped] == ["vite"]


class TestUpdateNodeResyncsOnApplyFailure:
    """A package that fails inside _apply_node_package can still have
    mutated node_modules before its own manifest rollback ran (pnpm add
    succeeded, dedupe didn't) — update_node's loop must resync node_modules
    on that failure path so the next package in the same workspace isn't
    checked against a tree that no longer matches the restored manifests.
    """

    def test_resync_called_after_apply_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release_dt = datetime.now(UTC) - timedelta(days=30)

        def fake_registry_data(
            name: str, cache: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            return {"time": {"5.1.0": release_dt.isoformat()}}

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            stdout = json.dumps({"svelte": {"current": "5.0.0", "latest": "5.1.0"}})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        def fake_apply(workspace: str, name: str, version: str) -> tuple[bool, str]:
            return False, "dedupe failed — boom"

        resync_calls = 0

        def fake_resync() -> bool:
            nonlocal resync_calls
            resync_calls += 1
            return True

        monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
        monkeypatch.setattr(deps_update, "_run", fake_run)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)

        updated, _skipped, errors = deps_update.update_node(10, "frontend")

        assert updated == []
        assert len(errors) == 1
        assert "dedupe failed" in errors[0]
        assert resync_calls == 1


class TestRegistryCacheAcrossWorkspaces:
    """A shared registry_cache must save repeat npm fetches for a package
    outdated in more than one workspace's package.json (e.g. @types/node
    kept version-aligned across root/frontend/electron) — but only once a
    fetch has actually succeeded, so a transient failure stays retryable.
    """

    def test_cache_hit_skips_the_network_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache: dict[str, dict[str, Any]] = {"svelte": {"time": {"5.1.0": "x"}}}
        calls = 0

        def fake_fetch_json(url: str, *, headers: Any = None) -> dict[str, Any] | None:
            nonlocal calls
            calls += 1
            return {"time": {}}

        monkeypatch.setattr(deps_update, "_fetch_json", fake_fetch_json)

        data = deps_update._npm_registry_data("svelte", cache)

        assert data == {"time": {"5.1.0": "x"}}
        assert calls == 0

    def test_failure_is_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cache: dict[str, dict[str, Any]] = {}
        calls = 0

        def fake_fetch_json(url: str, *, headers: Any = None) -> dict[str, Any] | None:
            nonlocal calls
            calls += 1
            return None

        monkeypatch.setattr(deps_update, "_fetch_json", fake_fetch_json)

        first = deps_update._npm_registry_data("svelte", cache)
        second = deps_update._npm_registry_data("svelte", cache)

        assert first is None
        assert second is None
        assert calls == 2  # not cached, so both calls hit the network
        assert "svelte" not in cache

    def test_success_is_cached_and_not_refetched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache: dict[str, dict[str, Any]] = {}
        calls = 0

        def fake_fetch_json(url: str, *, headers: Any = None) -> dict[str, Any] | None:
            nonlocal calls
            calls += 1
            return {"time": {"5.1.0": "2025-01-01T00:00:00Z"}}

        monkeypatch.setattr(deps_update, "_fetch_json", fake_fetch_json)

        first = deps_update._npm_registry_data("svelte", cache)
        second = deps_update._npm_registry_data("svelte", cache)

        assert first == second
        assert calls == 1


class TestMainExceptionIsolation:
    """The module docstring promises a hard error in one workspace/ecosystem
    never prevents a PR for the others — that promise is only real if it
    holds for unexpected exceptions, not just the (ok, reason) tuples the
    functions normally return. See scripts/deps_update.py's earlier bug
    where an exception from update_node/_verify_node/_verify_python on one
    workspace crashed main() before the PR body (and CHANGELOG entry) for
    every other workspace's already-applied updates was ever written.
    """

    def test_update_node_exception_does_not_abort_other_workspaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release_dt = datetime.now(UTC) - timedelta(days=30)

        def fake_update_node(
            cooldown: int, workspace: str, registry_cache: dict[str, Any] | None = None
        ) -> tuple[list[Any], list[Any], list[str]]:
            if workspace == "analecta":
                raise RuntimeError("pnpm outdated returned garbage")
            return [("svelte", "5.0.0", "5.1.0", release_dt)], [], []

        def fake_update_python(cooldown: int) -> tuple[list[Any], list[Any], list[str]]:
            return [], [], []

        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(sys, "argv", ["deps_update.py"])

        deps_update.main()  # must return normally, not raise RuntimeError

    def test_verify_node_exception_still_writes_pr_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release_dt = datetime.now(UTC) - timedelta(days=30)

        def fake_update_python(cooldown: int) -> tuple[list[Any], list[Any], list[str]]:
            return [("ruff", "0.1.0", "0.2.0", release_dt)], [], []

        def fake_update_node(
            cooldown: int, workspace: str, registry_cache: dict[str, Any] | None = None
        ) -> tuple[list[Any], list[Any], list[str]]:
            if workspace == "frontend":
                return [("svelte", "5.0.0", "5.1.0", release_dt)], [], []
            return [], [], []

        def fake_verify_node(
            snapshots: dict[Path, bytes], batch: list[Any]
        ) -> tuple[list[Any], list[Any], list[str]]:
            raise RuntimeError("check.sh subprocess vanished")

        def fake_verify_python(
            backend: Path, uv_lock_path: Path, snap: bytes, batch: list[Any]
        ) -> tuple[list[Any], list[Any]]:
            return batch, []

        pr_body_file = tmp_path / "pr-body.md"
        for name in deps_update._WORKSPACE_DIR.values():
            pkg = (
                tmp_path / name / "package.json"
                if name != "."
                else tmp_path / "package.json"
            )
            pkg.parent.mkdir(parents=True, exist_ok=True)
            pkg.write_text("{}")
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
        (tmp_path / "backend").mkdir(exist_ok=True)
        (tmp_path / "backend" / "uv.lock").write_text("")
        (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n")

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "_verify_python", fake_verify_python)
        monkeypatch.setattr(deps_update, "_verify_node", fake_verify_node)
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--verify", "--pr-body-file", str(pr_body_file)],
        )

        deps_update.main()  # must not raise, and must still write the PR body

        body = pr_body_file.read_text()
        assert "`ruff`" in body
        assert "_verify_node crashed unexpectedly" in body

    def test_verify_python_exception_restores_uv_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_verify_python's own rollback (restoring step_snap) only runs on
        its normal bisection-failure path — an exception raised mid-batch
        must not leave uv.lock carrying updates the PR body then claims (via
        an emptied py_up) were never applied. main()'s except block must
        restore the pre-batch snapshot itself.
        """
        release_dt = datetime.now(UTC) - timedelta(days=30)
        original_lock = b"lockfileVersion: original\n"

        (tmp_path / "backend").mkdir()
        uv_lock_path = tmp_path / "backend" / "uv.lock"
        uv_lock_path.write_bytes(original_lock)
        for name in deps_update._WORKSPACE_DIR.values():
            pkg = (
                tmp_path / name / "package.json"
                if name != "."
                else tmp_path / "package.json"
            )
            pkg.parent.mkdir(parents=True, exist_ok=True)
            pkg.write_text("{}")
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
        (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n")

        def fake_update_python(cooldown: int) -> tuple[list[Any], list[Any], list[str]]:
            return [("ruff", "0.1.0", "0.2.0", release_dt)], [], []

        def fake_update_node(
            cooldown: int, workspace: str, registry_cache: dict[str, Any] | None = None
        ) -> tuple[list[Any], list[Any], list[str]]:
            return [], [], []

        def fake_verify_python(
            backend: Path, uv_lock_path: Path, snap: bytes, batch: list[Any]
        ) -> tuple[list[Any], list[Any]]:
            # Simulate a partial bisection write before the crash — this is
            # the state main()'s except block must undo.
            uv_lock_path.write_bytes(b"lockfileVersion: mid-bisection\n")
            raise RuntimeError("uv subprocess vanished")

        pr_body_file = tmp_path / "pr-body.md"

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "_verify_python", fake_verify_python)
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--verify", "--pr-body-file", str(pr_body_file)],
        )

        # Nothing succeeded anywhere in this run (py_up got zeroed by the
        # crash, no Node updates), so main() exits 1 by its own "only fail
        # loudly when nothing succeeded" rule — the PR body write happens
        # before that exit check, so it's still there to assert on.
        with pytest.raises(SystemExit):
            deps_update.main()

        assert uv_lock_path.read_bytes() == original_lock
        body = pr_body_file.read_text()
        assert "`ruff`" not in body
        assert "_verify_python crashed unexpectedly" in body

    def test_verify_node_exception_restores_node_snap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirrors test_verify_python_exception_restores_uv_lock above: an
        exception raised mid-verification must not leave pnpm-lock.yaml
        carrying a partial mutation the PR body then claims (via an emptied
        nd_up_tagged) was never applied.
        """
        release_dt = datetime.now(UTC) - timedelta(days=30)
        original_lock = b"lockfileVersion: original\n"

        for name in deps_update._WORKSPACE_DIR.values():
            pkg = (
                tmp_path / name / "package.json"
                if name != "."
                else tmp_path / "package.json"
            )
            pkg.parent.mkdir(parents=True, exist_ok=True)
            pkg.write_text("{}")
        lock_path = tmp_path / "pnpm-lock.yaml"
        lock_path.write_bytes(original_lock)
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "uv.lock").write_text("")
        (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n")

        def fake_update_python(cooldown: int) -> tuple[list[Any], list[Any], list[str]]:
            return [], [], []

        def fake_update_node(
            cooldown: int, workspace: str, registry_cache: dict[str, Any] | None = None
        ) -> tuple[list[Any], list[Any], list[str]]:
            if workspace == "frontend":
                return [("svelte", "5.0.0", "5.1.0", release_dt)], [], []
            return [], [], []

        def fake_verify_node(
            snapshots: dict[Path, bytes], batch: list[Any]
        ) -> tuple[list[Any], list[Any], list[str]]:
            # Simulate a partial bisection write before the crash — this is
            # the state main()'s except block must undo.
            lock_path.write_bytes(b"lockfileVersion: mid-bisection\n")
            raise RuntimeError("check.sh subprocess vanished")

        pr_body_file = tmp_path / "pr-body.md"

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "_verify_node", fake_verify_node)
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--verify", "--pr-body-file", str(pr_body_file)],
        )

        # Nothing succeeded anywhere (nd_up_tagged got zeroed by the crash,
        # no Python updates), so main() exits 1 by its own "only fail loudly
        # when nothing succeeded" rule — the PR body write happens before
        # that exit check, so it's still there to assert on.
        with pytest.raises(SystemExit):
            deps_update.main()

        assert lock_path.read_bytes() == original_lock
        body = pr_body_file.read_text()
        assert "`svelte`" not in body
        assert "_verify_node crashed unexpectedly" in body

    def test_update_node_exception_preserves_prior_workspace_and_resyncs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash in one workspace's update_node() call must restore only
        that workspace's own damage to the *shared* pnpm-lock.yaml, not the
        single global pre-run snapshot — the latter would also wipe out an
        earlier workspace's already-applied, already-tracked update still on
        disk. The crash path must also resync node_modules so the environment
        is trustworthy again before check.sh (via _verify_node) runs on
        whatever the surviving workspaces contributed.
        """
        release_dt = datetime.now(UTC) - timedelta(days=30)
        original_lock = b"lockfileVersion: original\n"
        workspace_dirs = {"frontend": "frontend", "electron-ws": "electron"}
        monkeypatch.setattr(deps_update, "_WORKSPACE_DIR", workspace_dirs)

        for rel_dir in workspace_dirs.values():
            pkg = tmp_path / rel_dir / "package.json"
            pkg.parent.mkdir(parents=True, exist_ok=True)
            pkg.write_text("{}")
        lock_path = tmp_path / "pnpm-lock.yaml"
        lock_path.write_bytes(original_lock)
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "uv.lock").write_text("")
        (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n")

        applied_frontend_pkg = b'{"dependencies": {"svelte": "5.1.0"}}'

        def fake_update_python(cooldown: int) -> tuple[list[Any], list[Any], list[str]]:
            return [], [], []

        def fake_update_node(
            cooldown: int, workspace: str, registry_cache: dict[str, Any] | None = None
        ) -> tuple[list[Any], list[Any], list[str]]:
            if workspace == "frontend":
                (tmp_path / "frontend" / "package.json").write_bytes(
                    applied_frontend_pkg
                )
                return [("svelte", "5.0.0", "5.1.0", release_dt)], [], []
            # electron-ws: partially mutate the shared lockfile, then crash.
            lock_path.write_bytes(b"lockfileVersion: mid-crash\n")
            raise RuntimeError("pnpm add vanished mid-apply")

        def fake_verify_node(
            snapshots: dict[Path, bytes], batch: list[Any]
        ) -> tuple[list[Any], list[Any], list[str]]:
            return batch, [], []

        resync_calls = 0

        def fake_resync() -> bool:
            nonlocal resync_calls
            resync_calls += 1
            return True

        pr_body_file = tmp_path / "pr-body.md"

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "_verify_node", fake_verify_node)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--verify", "--pr-body-file", str(pr_body_file)],
        )

        deps_update.main()  # must not raise — frontend's update still lands

        # frontend's legitimate update survives the sibling workspace's crash.
        assert (tmp_path / "frontend" / "package.json").read_bytes() == (
            applied_frontend_pkg
        )
        # electron-ws's own partial mutation to the *shared* lockfile is
        # reverted — not the frontend workspace's contribution to it.
        assert lock_path.read_bytes() == original_lock
        assert resync_calls == 1

        body = pr_body_file.read_text()
        assert "`svelte`" in body
        assert "`electron-ws`: update_node crashed unexpectedly" in body
