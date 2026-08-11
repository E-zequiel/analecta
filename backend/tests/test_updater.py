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
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            if workspace == "analecta":
                errors.append("All npm registry fetches failed")
                return [], []
            return [("svelte", "5.0.0", "5.1.0", release_dt)], []

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(sys, "argv", ["deps_update.py"])

        deps_update.main()  # must return normally, not sys.exit(1)

    def test_total_failure_still_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If nothing succeeded anywhere and something errored, fail loudly."""

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            errors.append("All npm registry fetches failed")
            return [], []

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

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
            nd_blocked_ws=[],
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
            nd_blocked_ws=[],
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
            nd_blocked_ws=[],
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
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            errors.append("other-pkg: uv lock failed — boom")
            return applied, []

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        verify_calls: list[list[Any]] = []

        def fake_verify_python(
            backend: Path,
            uv_lock_path: Path,
            snapshot: dict[Path, bytes],
            batch: list[Any],
            survivors: list[Any],
            blocked: list[Any],
            errors: list[str],
        ) -> None:
            verify_calls.append(batch)
            survivors.extend(batch)

        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "_verify_python", fake_verify_python)
        monkeypatch.setattr(sys, "argv", ["deps_update.py", "--verify"])

        deps_update.main()

        assert verify_calls == [applied]


class TestGuardRestoreFailureAndInterrupt:
    """_guard()'s own restore step, and KeyboardInterrupt raised by fn(),
    must both be handled without letting a new, unrelated exception escape
    _guard uncaught — see its docstring: a disk-full condition that caused
    fn()'s crash can just as easily break the write-back, and a manual
    Ctrl+C must still leave state consistent rather than aborting
    mid-mutation with nothing recorded.
    """

    def test_restore_failure_is_recorded_not_raised(self) -> None:
        def boom_fn() -> None:
            raise RuntimeError("original crash")

        snapshot = {Path("/nonexistent-dir/does-not-exist"): b"data"}
        errors: list[str] = []

        result = deps_update._guard("thing", errors, snapshot, boom_fn)

        assert result is None
        assert any("thing crashed unexpectedly — original crash" in e for e in errors)
        assert any("thing restore failed unexpectedly" in e for e in errors)

    def test_keyboard_interrupt_restores_records_and_reraises(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.txt"
        path.write_bytes(b"mutated-mid-run")

        def interrupt_fn() -> None:
            raise KeyboardInterrupt

        errors: list[str] = []

        with pytest.raises(KeyboardInterrupt):
            deps_update._guard("thing", errors, {path: b"original"}, interrupt_fn)

        assert path.read_bytes() == b"original"
        assert any("thing crashed unexpectedly" in e for e in errors)

    def test_keyboard_interrupt_runs_on_restore_hook_before_reraising(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.txt"
        path.write_bytes(b"original")

        def interrupt_fn() -> None:
            raise KeyboardInterrupt

        hook_calls = 0

        def hook() -> None:
            nonlocal hook_calls
            hook_calls += 1

        errors: list[str] = []

        with pytest.raises(KeyboardInterrupt):
            deps_update._guard(
                "thing", errors, {path: b"original"}, interrupt_fn, on_restore=hook
            )

        assert hook_calls == 1

    def test_plain_exception_is_not_reraised(self) -> None:
        """A regular Exception must keep returning None, not re-raise —
        only KeyboardInterrupt gets the re-raise treatment.
        """

        def boom_fn() -> None:
            raise RuntimeError("ordinary crash")

        errors: list[str] = []

        result = deps_update._guard("thing", errors, {}, boom_fn)

        assert result is None


class TestResyncNodeModulesExceptionSafety:
    """Every call site treats `_resync_node_modules()`'s bool return as the
    sole failure signal — an unexpected raise from `_run` (e.g. pnpm
    vanishing from PATH mid-run) must be converted to a plain False instead
    of escaping as an exception, or it would abort the caller's loop
    mid-iteration instead of letting it react the same way it already does
    to a bad pnpm returncode.
    """

    def test_run_exception_is_converted_to_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("pnpm: command not found")

        monkeypatch.setattr(deps_update, "_run", fake_run)

        assert deps_update._resync_node_modules() is False


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

        errors: list[str] = []
        ok, reason = deps_update._apply_node_package(
            "frontend", "svelte", "5.1.0", errors
        )

        assert ok is False
        assert "disk full" in reason
        assert pkg_path.read_text() == original_pkg
        assert lock_path.read_text() == original_lock
        assert errors == []  # restore itself succeeded, nothing to record

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

        errors: list[str] = []
        ok, reason = deps_update._apply_node_package(
            "frontend", "svelte", "5.1.0", errors
        )

        assert ok is False
        assert "unexpected shape" in reason
        assert pkg_path.read_text() == original_pkg
        assert lock_path.read_text() == original_lock
        assert errors == []  # restore itself succeeded, nothing to record

    def test_finally_restore_failure_does_not_mask_the_original_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A prior version's bare `finally: write_bytes(...)` would let a
        write failure there (e.g. the same disk-full condition that caused
        dedupe to fail in the first place) raise out of the finally block —
        Python's finally-masks-pending-return semantics would silently
        replace the already-computed (False, "dedupe failed...") with an
        unrelated, uncaught exception. The restore is now guarded, so the
        original (False, reason) survives even when the restore itself
        fails. A still-later version printed the restore failure to stdout
        only, without recording it in *errors* — the PR body's Errors
        section never mentioned that the manifest pair might now be
        mutually inconsistent. It must now reach *errors* too.
        """
        pkg_dir = tmp_path / "frontend"
        pkg_dir.mkdir()
        pkg_path = pkg_dir / "package.json"
        lock_path = tmp_path / "pnpm-lock.yaml"
        pkg_path.write_text('{"dependencies": {"svelte": "5.0.0"}}')
        lock_path.write_text("lockfileVersion: 9\n")

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "_WORKSPACE_DIR", {"frontend": "frontend"})

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if cmd[:2] == ["pnpm", "dedupe"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fake_ensure_exact_specifier(
            workspace_dir: str, name: str, version: str
        ) -> bool:
            return False

        restore_calls = 0
        real_write_bytes = Path.write_bytes

        def flaky_write_bytes(self: Path, data: bytes) -> int:
            nonlocal restore_calls
            restore_calls += 1
            if self == pkg_path:
                raise OSError("disk full")
            return real_write_bytes(self, data)

        monkeypatch.setattr(deps_update, "_run", fake_run)
        monkeypatch.setattr(
            deps_update, "_ensure_exact_specifier", fake_ensure_exact_specifier
        )
        monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

        errors: list[str] = []
        ok, reason = deps_update._apply_node_package(
            "frontend", "svelte", "5.1.0", errors
        )

        assert ok is False
        assert "dedupe failed" in reason
        assert "boom" in reason
        assert restore_calls == 1  # attempted pkg_path first, raised, stopped there
        assert len(errors) == 1
        assert "failed to restore" in errors[0]
        assert "disk full" in errors[0]


class TestApplyPythonPackageExceptionSafety:
    """uv.lock's own write (per its upstream source) is a direct, non-atomic
    fs write, not temp-file-then-rename — a killed/crashed `uv` subprocess
    mid-write can leave it truncated even behind a plain non-zero
    returncode, which update_python's loop treats as a handled per-package
    failure, not a crash _guard() would restore from. _apply_python_package
    must therefore give the same (False, reason)-leaves-uv.lock-untouched
    guarantee _apply_node_package already gives for
    package.json/pnpm-lock.yaml, for the same reason.
    """

    def test_restores_uv_lock_after_a_mid_write_partial_mutation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The actual gap this class exists to close, not just the
        finally-guard's own exception safety: a subprocess killed mid-write
        (OOM, disk-full, SIGKILL — surfacing to subprocess.run as a
        negative returncode) can leave uv.lock mutated behind a plain
        (False, reason) return, no exception raised at all. That's a
        handled failure update_python's loop just continues past — nothing
        else in this file would ever restore uv.lock for it.
        """
        backend = tmp_path / "backend"
        backend.mkdir()
        lock_path = backend / "uv.lock"
        original_lock = '[[package]]\nname = "ruff"\nversion = "0.1.0"\n'
        lock_path.write_text(original_lock)

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            lock_path.write_bytes(b"truncated")
            return subprocess.CompletedProcess(cmd, -9, stdout="", stderr="")

        monkeypatch.setattr(deps_update, "_run", fake_run)

        errors: list[str] = []
        ok, _reason = deps_update._apply_python_package("ruff", backend, errors)

        assert ok is False
        assert lock_path.read_text() == original_lock
        assert errors == []  # restore itself succeeded, nothing to record

    def test_restores_uv_lock_on_unexpected_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = tmp_path / "backend"
        backend.mkdir()
        lock_path = backend / "uv.lock"
        original_lock = '[[package]]\nname = "ruff"\nversion = "0.1.0"\n'
        lock_path.write_text(original_lock)

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            raise OSError("disk full")

        monkeypatch.setattr(deps_update, "_run", fake_run)

        errors: list[str] = []
        ok, reason = deps_update._apply_python_package("ruff", backend, errors)

        assert ok is False
        assert "disk full" in reason
        assert lock_path.read_text() == original_lock
        assert errors == []  # restore itself succeeded, nothing to record

    def test_restores_uv_lock_on_unexpected_non_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirrors _apply_node_package's equivalent test: an except clause
        narrowed to OSError alone would let any other exception type
        propagate straight past the finally's restore.
        """
        backend = tmp_path / "backend"
        backend.mkdir()
        lock_path = backend / "uv.lock"
        original_lock = '[[package]]\nname = "ruff"\nversion = "0.1.0"\n'
        lock_path.write_text(original_lock)

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            raise ValueError("unexpected shape")

        monkeypatch.setattr(deps_update, "_run", fake_run)

        errors: list[str] = []
        ok, reason = deps_update._apply_python_package("ruff", backend, errors)

        assert ok is False
        assert "unexpected shape" in reason
        assert lock_path.read_text() == original_lock
        assert errors == []  # restore itself succeeded, nothing to record

    def test_finally_restore_failure_does_not_mask_the_original_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bare `finally: write_bytes(...)` would let a restore failure
        there (e.g. the same disk-full condition that failed `uv lock`
        itself) raise out of the finally block, silently replacing the
        already-computed (False, reason) with an unrelated, uncaught
        exception — same regression class as _apply_node_package's
        equivalent test.
        """
        backend = tmp_path / "backend"
        backend.mkdir()
        lock_path = backend / "uv.lock"
        lock_path.write_text('[[package]]\nname = "ruff"\nversion = "0.1.0"\n')

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        real_write_bytes = Path.write_bytes

        def flaky_write_bytes(self: Path, data: bytes) -> int:
            if self == lock_path:
                raise OSError("disk full")
            return real_write_bytes(self, data)

        monkeypatch.setattr(deps_update, "_run", fake_run)
        monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

        errors: list[str] = []
        ok, reason = deps_update._apply_python_package("ruff", backend, errors)

        assert ok is False
        assert "uv lock failed" in reason
        assert "boom" in reason
        assert len(errors) == 1
        assert "failed to restore" in errors[0]
        assert "disk full" in errors[0]


class TestEnsureExactSpecifierScopedToDependencies:
    """A bare first-match regex over the whole package.json text could hit a
    same-named key in `scripts` before ever reaching the real specifier in
    `devDependencies` — reachable now that the root package.json (whose
    `scripts` block precedes `devDependencies`) is a workspace this function
    runs against. The search must be scoped to start at the first
    dependencies-flavored key.
    """

    def test_same_named_script_key_is_not_mistaken_for_the_specifier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pkg_path = tmp_path / "package.json"
        pkg_path.write_text(
            "{\n"
            '  "scripts": {\n'
            '    "eslint": "eslint ."\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "eslint": "10.4.0"\n'
            "  }\n"
            "}\n"
        )
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)

        changed = deps_update._ensure_exact_specifier(".", "eslint", "10.5.0")

        assert changed is True
        text = pkg_path.read_text()
        assert '"eslint": "eslint ."' in text
        assert '"eslint": "10.5.0"' in text
        assert text.count('"10.5.0"') == 1

    def test_rewrites_the_specifier_with_no_preceding_scripts_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pkg_path = tmp_path / "frontend" / "package.json"
        pkg_path.parent.mkdir()
        pkg_path.write_text('{\n  "dependencies": {\n    "svelte": "^5.0.0"\n  }\n}\n')
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)

        changed = deps_update._ensure_exact_specifier("frontend", "svelte", "5.1.0")

        assert changed is True
        assert '"svelte": "5.1.0"' in pkg_path.read_text()

    def test_keywords_string_value_is_not_mistaken_for_a_dependency_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The old `r'"\\w*[Dd]ependencies"'` scan matched any quoted string
        ending in that word, key or not — a `keywords` array entry literally
        reading "dependencies" would anchor the search there instead of the
        real `devDependencies` block, still-unmatched-key regex behavior
        aside, because it never required a trailing colon.
        """
        pkg_path = tmp_path / "package.json"
        pkg_path.write_text(
            "{\n"
            '  "keywords": ["dependencies", "cli"],\n'
            '  "devDependencies": {\n'
            '    "foo": "^1.0.0"\n'
            "  }\n"
            "}\n"
        )
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)

        changed = deps_update._ensure_exact_specifier(".", "foo", "2.0.0")

        assert changed is True
        text = pkg_path.read_text()
        assert '"keywords": ["dependencies", "cli"]' in text
        assert '"foo": "2.0.0"' in text

    def test_script_key_shaped_like_a_dependency_key_is_not_a_block_opener(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unlike a bare `keywords` string, a script literally named
        `checkDependencies` does have a trailing colon — only an explicit
        allow-list of the six real npm dependency-type keys (not any
        `\\w*[Dd]ependencies`-shaped key) keeps this from being treated as a
        block opener.
        """
        pkg_path = tmp_path / "package.json"
        pkg_path.write_text(
            "{\n"
            '  "scripts": {\n'
            '    "checkDependencies": "eslint --dependencies"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "foo": "^1.0.0"\n'
            "  }\n"
            "}\n"
        )
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)

        changed = deps_update._ensure_exact_specifier(".", "foo", "2.0.0")

        assert changed is True
        text = pkg_path.read_text()
        assert '"checkDependencies": "eslint --dependencies"' in text
        assert '"foo": "2.0.0"' in text

    def test_same_package_in_two_dependency_blocks_only_rewrites_the_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The search is scoped to each dependency-type block's own brace
        span, tried in file order — a bare regex anchored only at the start
        of the first dependency-type key (with no block-end boundary) could
        still leak past it into a later, unrelated block if the name wasn't
        found in the first one it should have matched.
        """
        pkg_path = tmp_path / "package.json"
        pkg_path.write_text(
            "{\n"
            '  "peerDependencies": {\n'
            '    "bar": "^1.0.0"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "bar": "^3.0.0"\n'
            "  }\n"
            "}\n"
        )
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)

        changed = deps_update._ensure_exact_specifier(".", "bar", "4.0.0")

        assert changed is True
        text = pkg_path.read_text()
        assert '"peerDependencies": {\n    "bar": "4.0.0"' in text
        assert '"devDependencies": {\n    "bar": "^3.0.0"' in text


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

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
            return True, ""

        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)

        survivors: list[Any] = []
        blocked: list[Any] = []
        errors: list[str] = []
        deps_update._verify_node(snapshots, batch, survivors, blocked, errors)

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

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
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

        survivors: list[Any] = []
        blocked: list[Any] = []
        errors: list[str] = []
        deps_update._verify_node(snapshots, batch, survivors, blocked, errors)

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

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
            return True, ""

        # First call is the initial full-batch restore (succeeds); second
        # call is the per-step resync after isolating "svelte" (fails).
        resync_results = iter([True, False])

        def fake_resync() -> bool:
            return next(resync_results)

        monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)

        survivors: list[Any] = []
        blocked: list[Any] = []
        errors: list[str] = []
        deps_update._verify_node(snapshots, batch, survivors, blocked, errors)

        assert survivors == []
        assert [name for _, (name, _, _) in blocked] == ["svelte"]
        assert len(errors) == 1
        assert "node_modules resync failed" in errors[0]
        assert "1 package(s) dropped from this batch" in errors[0]


class TestVerifyResultsSurviveMidBatchCrash:
    """_verify_python/_verify_node write into caller-owned survivors/blocked
    lists rather than building them locally and returning at the end — a
    crash on a *later* iteration of the bisection loop (not the first) must
    not discard packages the loop already confirmed or ruled out earlier in
    the same call, the same way update_python/update_node's caller-owned
    *errors* list already survives a mid-loop crash.
    """

    def test_verify_python_preserves_earlier_survivor_on_later_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mocked _apply_python_package that never writes real bytes can't
        tell a "still-applied on disk" survivor apart from a fully-reverted
        one — both leave uv.lock at the same untouched bytes, so this test
        must simulate a real `uv lock --upgrade-package` mutation to be able
        to assert on the actual regression: _guard restoring past a
        confirmed survivor's own change, not just past the crashing
        candidate's.
        """
        backend = tmp_path / "backend"
        uv_lock_path = tmp_path / "uv.lock"
        pristine = b"original"
        uv_lock_path.write_bytes(pristine)
        release_dt = datetime.now(UTC) - timedelta(days=30)
        batch = [
            ("pkg-a", "1.0.0", "1.1.0", release_dt),
            ("pkg-b", "2.0.0", "2.1.0", release_dt),
        ]

        # Full-batch check.sh fails, then pkg-a's own isolation check passes.
        check_results = iter([False, True])

        def fake_run_check(repo_root: Path, scope: str) -> bool:
            return next(check_results)

        def fake_apply(name: str, backend: Path, errors: list[str]) -> tuple[bool, str]:
            if name == "pkg-b":
                raise RuntimeError("uv subprocess vanished")
            uv_lock_path.write_bytes(pristine + b"+pkg-a")
            return True, ""

        monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
        monkeypatch.setattr(deps_update, "_apply_python_package", fake_apply)

        survivors: list[Any] = []
        blocked: list[Any] = []
        errors: list[str] = []
        snapshot = {uv_lock_path: pristine}

        def run_verify_python() -> None:
            deps_update._verify_python(
                backend, uv_lock_path, snapshot, batch, survivors, blocked, errors
            )

        result = deps_update._guard(
            "_verify_python", errors, snapshot, run_verify_python
        )

        assert result is None  # _guard caught pkg-b's crash
        assert survivors == [("pkg-a", "1.0.0", "1.1.0", release_dt)]
        assert any("_verify_python crashed unexpectedly" in e for e in errors)
        # The regression this guards: _guard must not restore all the way
        # back to the pre-batch pristine bytes once pkg-a's own change is
        # confirmed on disk — that would silently contradict what
        # `survivors` (preserved through the crash) still reports as
        # applied, so the committed uv.lock and the PR body would disagree.
        assert uv_lock_path.read_bytes() == pristine + b"+pkg-a"

    def test_verify_node_preserves_earlier_survivor_on_later_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """See test_verify_python_preserves_earlier_survivor_on_later_crash's
        docstring for why fake_apply must write real bytes, not just return
        (True, "")."""
        pkg_path = tmp_path / "package.json"
        lock_path = tmp_path / "pnpm-lock.yaml"
        pkg_pristine = b"{}"
        lock_pristine = b"lockfileVersion: 9\n"
        pkg_path.write_bytes(pkg_pristine)
        lock_path.write_bytes(lock_pristine)
        snapshots = {pkg_path: pkg_pristine, lock_path: lock_pristine}

        release_dt = datetime.now(UTC) - timedelta(days=30)
        batch = [
            ("frontend", ("svelte", "5.0.0", "5.1.0", release_dt)),
            ("frontend", ("vite", "6.0.0", "6.1.0", release_dt)),
        ]

        # Full-batch check.sh fails, then svelte's own isolation check passes.
        check_results = iter([False, True])

        def fake_run_check(repo_root: Path, scope: str) -> bool:
            return next(check_results)

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
            if name == "vite":
                raise RuntimeError("check.sh subprocess vanished")
            pkg_path.write_bytes(pkg_pristine + b"svelte")
            lock_path.write_bytes(lock_pristine + b"svelte\n")
            return True, ""

        monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)
        monkeypatch.setattr(deps_update, "_resync_node_modules", lambda: True)

        survivors: list[Any] = []
        blocked: list[Any] = []
        errors: list[str] = []

        def run_verify_node() -> None:
            deps_update._verify_node(snapshots, batch, survivors, blocked, errors)

        result = deps_update._guard("_verify_node", errors, snapshots, run_verify_node)

        assert result is None  # _guard caught vite's crash
        assert [name for _, (name, *_rest) in survivors] == ["svelte"]
        assert any("_verify_node crashed unexpectedly" in e for e in errors)
        # The regression this guards: _guard must not restore both files all
        # the way back to the pre-batch pristine bytes once svelte's own
        # change is confirmed on disk — that would silently contradict what
        # `survivors` (preserved through the crash) still reports as
        # applied, so the committed manifests and the PR body would
        # disagree.
        assert pkg_path.read_bytes() == pkg_pristine + b"svelte"
        assert lock_path.read_bytes() == lock_pristine + b"svelte\n"


class TestBlockedEntriesAreWorkspaceTagged:
    """Blocked, unlike Updated, previously had no workspace field — a
    package blocked in two different workspaces in the same run (e.g.
    @types/node kept version-aligned across frontend/electron) rendered as
    two byte-identical PR-body entries with no way to tell them apart.
    _verify_node now tags each blocked entry with its own workspace, and
    _pr_body renders it.
    """

    def test_verify_node_tags_blocked_entries_by_workspace(
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
        batch = [
            ("frontend", ("@types/node", "20.0.0", "21.0.0", release_dt)),
            ("electron", ("@types/node", "20.0.0", "21.0.0", release_dt)),
        ]

        def fake_run_check(repo_root: Path, scope: str) -> bool:
            return False  # full batch fails, and every isolation check fails

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
            return True, ""

        monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)
        monkeypatch.setattr(deps_update, "_resync_node_modules", lambda: True)

        survivors: list[Any] = []
        blocked: list[Any] = []
        errors: list[str] = []
        deps_update._verify_node(snapshots, batch, survivors, blocked, errors)

        assert [ws for ws, _ in blocked] == ["frontend", "electron"]
        assert [name for _, (name, _, _) in blocked] == ["@types/node"] * 2

    def test_pr_body_distinguishes_same_package_blocked_in_two_workspaces(
        self,
    ) -> None:
        blocked = [
            ("@types/node", "21.0.0", "check.sh frontend failed"),
            ("@types/node", "21.0.0", "check.sh frontend failed"),
        ]
        body = _pr_body(
            py_up=[],
            py_sk=[],
            py_blocked=[],
            py_errors=[],
            nd_up=[],
            nd_sk=[],
            nd_blocked=blocked,
            nd_errors=[],
            nd_error_ws=[],
            nd_ws=[],
            nd_blocked_ws=["frontend", "electron"],
            cooldown=10,
        )
        assert "`@types/node` 21.0.0 (`frontend`)" in body
        assert "`@types/node` 21.0.0 (`electron`)" in body

    def test_pr_body_omits_workspace_parens_for_python(self) -> None:
        """py_blocked has no workspace concept (single implicit workspace)
        — _section's default blocked_workspaces=None must not render a
        stray `(None)` for it.
        """
        blocked = [("ruff", "0.2.0", "check.sh backend failed")]
        body = _pr_body(
            py_up=[],
            py_sk=[],
            py_blocked=blocked,
            py_errors=[],
            nd_up=[],
            nd_sk=[],
            nd_blocked=[],
            nd_errors=[],
            nd_error_ws=[],
            nd_ws=[],
            nd_blocked_ws=[],
            cooldown=10,
        )
        assert "`ruff` 0.2.0 — _check.sh backend failed_" in body
        assert "(None)" not in body
        assert "(`" not in body.split("### Node")[0]


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

        errors: list[str] = []
        updated, skipped = deps_update.update_python(10, errors)

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

        errors: list[str] = []
        _updated, skipped = deps_update.update_python(10, errors)

        assert errors == []
        assert [name for name, _, _ in skipped] == ["httpx2"]

    def test_all_fetches_failing_escalates_with_exact_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reaches _record_registry_health's `if fetch_attempted > 0 and
        fetch_ok == 0` branch — untested until now, unlike its `elif`
        sibling covered above. Asserts the full string rather than a
        substring: the message is built from two concatenated literals, and
        a substring check wouldn't catch a wrong space landing at the join.
        """
        self._backend(tmp_path)
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)

        def fake_fetch_json(url: str, *, headers: Any = None) -> dict[str, Any] | None:
            return None  # registry unreachable for every package

        monkeypatch.setattr(deps_update, "_fetch_json", fake_fetch_json)

        errors: list[str] = []
        updated, skipped = deps_update.update_python(10, errors)

        assert updated == []
        assert skipped == []
        assert len(errors) == 1
        assert errors[0] == (
            "All PyPI registry fetches failed — no packages could be checked"
        )


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

        errors: list[str] = []
        updated, skipped = deps_update.update_node(10, "frontend", errors)

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

        errors: list[str] = []
        _updated, skipped = deps_update.update_node(10, "frontend", errors)

        assert errors == []
        assert [name for name, _, _ in skipped] == ["vite"]

    def test_mixed_fetch_failure_and_missing_timestamp_escalates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A total fetch failure for one candidate alongside a missing
        timestamp for another must still add up to "every candidate went
        unevaluated". Regression test for moving `candidates` to only count
        after a successful fetch (mirroring update_python's ordering):
        counting it earlier, right after `pnpm outdated` confirms an update
        exists, let a package whose fetch failed outright inflate
        `candidates` without ever touching `date_unknown` — a workspace
        mixing both failure types (fetch_ok > 0, date_unknown < candidates)
        cleared *both* escalation checks and reported zero errors despite
        100% of its candidates going unevaluated.
        """

        def fake_registry_data(
            name: str, cache: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            if name == "svelte":
                return None  # registry unreachable for this one
            return {"time": {}}  # reachable, but no entry for the target version

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

        errors: list[str] = []
        updated, skipped = deps_update.update_node(10, "frontend", errors)

        assert updated == []
        assert skipped == []
        assert len(errors) == 1
        assert "release-date lookups failed" in errors[0]

    def test_all_fetches_failing_escalates_with_exact_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirrors TestPypiReleaseDateFetchTracking's test of the same name —
        fixing this diagnostic gap in one ecosystem but not the other would
        leave the exact same untested branch on whichever side got skipped.
        """

        def fake_registry_data(
            name: str, cache: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            return None  # registry unreachable for every package

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            stdout = json.dumps({"svelte": {"current": "5.0.0", "latest": "5.1.0"}})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
        monkeypatch.setattr(deps_update, "_run", fake_run)

        errors: list[str] = []
        updated, skipped = deps_update.update_node(10, "frontend", errors)

        assert updated == []
        assert skipped == []
        assert len(errors) == 1
        assert errors[0] == (
            "All npm registry fetches failed — no packages could be checked"
        )


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

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
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

        errors: list[str] = []
        updated, _skipped = deps_update.update_node(10, "frontend", errors)

        assert updated == []
        assert len(errors) == 1
        assert "dedupe failed" in errors[0]
        assert resync_calls == 1

    def test_resync_failure_after_apply_failure_stops_the_workspace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed resync after an apply failure means node_modules can no
        longer be trusted to match the manifests (`_resync_node_modules`'s
        own docstring) — update_node must stop processing this workspace's
        remaining candidates rather than `continue` on to check them against
        a tree that might already be inconsistent, mirroring _verify_node's
        equivalent bisection loop, which already `break`s in this case.
        """
        release_dt = datetime.now(UTC) - timedelta(days=30)

        def fake_registry_data(
            name: str, cache: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            return {"time": {"2.0.0": release_dt.isoformat()}}

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            stdout = json.dumps(
                {
                    "pkg-a": {"current": "1.0.0", "latest": "2.0.0"},
                    "pkg-b": {"current": "1.0.0", "latest": "2.0.0"},
                }
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        apply_calls: list[str] = []

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
            apply_calls.append(name)
            return False, "dedupe failed — boom"

        def fake_resync() -> bool:
            return False

        monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
        monkeypatch.setattr(deps_update, "_run", fake_run)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)

        errors: list[str] = []
        updated, _skipped = deps_update.update_node(10, "frontend", errors)

        assert updated == []
        # pkg-a's apply failure is followed by a failed resync — the loop
        # must stop there rather than also attempting pkg-b against a tree
        # that can no longer be trusted.
        assert apply_calls == ["pkg-a"]
        assert len(errors) == 2
        assert "dedupe failed" in errors[0]
        assert "node_modules resync failed after pkg-a" in errors[1]
        assert "1 package(s) in this workspace dropped" in errors[1]


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
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            if workspace == "analecta":
                raise RuntimeError("pnpm outdated returned garbage")
            return [("svelte", "5.0.0", "5.1.0", release_dt)], []

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        resync_calls = 0

        def fake_resync() -> bool:
            nonlocal resync_calls
            resync_calls += 1
            return True

        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(sys, "argv", ["deps_update.py"])

        deps_update.main()  # must return normally, not raise RuntimeError

        assert resync_calls == 1

    def test_verify_node_exception_still_writes_pr_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release_dt = datetime.now(UTC) - timedelta(days=30)

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [("ruff", "0.1.0", "0.2.0", release_dt)], []

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            if workspace == "frontend":
                return [("svelte", "5.0.0", "5.1.0", release_dt)], []
            return [], []

        def fake_verify_node(
            snapshots: dict[Path, bytes],
            batch: list[Any],
            survivors: list[Any],
            blocked: list[Any],
            errors: list[str],
        ) -> None:
            raise RuntimeError("check.sh subprocess vanished")

        def fake_verify_python(
            backend: Path,
            uv_lock_path: Path,
            snapshot: dict[Path, bytes],
            batch: list[Any],
            survivors: list[Any],
            blocked: list[Any],
            errors: list[str],
        ) -> None:
            survivors.extend(batch)

        resync_calls = 0

        def fake_resync() -> bool:
            nonlocal resync_calls
            resync_calls += 1
            return True

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
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--verify", "--pr-body-file", str(pr_body_file)],
        )

        deps_update.main()  # must not raise, and must still write the PR body

        assert resync_calls == 1
        body = pr_body_file.read_text()
        assert "`ruff`" in body
        assert "_verify_node crashed unexpectedly" in body

    def test_update_python_exception_restores_uv_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirrors test_verify_python_exception_restores_uv_lock below, but
        for the earlier of the two Python _guard() sites: update_python()
        itself applies packages one at a time via `uv lock --upgrade-package`,
        so a crash partway through its own loop can leave uv.lock carrying
        an unverified mutation — main()'s _guard around update_python() must
        restore it, the same as the other three call sites already do.
        """
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

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            # Simulate one already-applied package before the crash — this
            # is the state main()'s except block must undo.
            uv_lock_path.write_bytes(b"lockfileVersion: mid-loop\n")
            raise RuntimeError("uv lock --upgrade-package vanished")

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        pr_body_file = tmp_path / "pr-body.md"

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
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
        assert "update_python crashed unexpectedly" in body

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

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [("ruff", "0.1.0", "0.2.0", release_dt)], []

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        def fake_verify_python(
            backend: Path,
            uv_lock_path: Path,
            snapshot: dict[Path, bytes],
            batch: list[Any],
            survivors: list[Any],
            blocked: list[Any],
            errors: list[str],
        ) -> None:
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
        nd_up_tagged) was never applied. Must also resync node_modules —
        restoring the manifests without it would leave node_modules holding
        whatever _verify_node's bisection last installed, a mismatch that
        wouldn't show up in `git status` since node_modules is gitignored.
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

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            if workspace == "frontend":
                return [("svelte", "5.0.0", "5.1.0", release_dt)], []
            return [], []

        def fake_verify_node(
            snapshots: dict[Path, bytes],
            batch: list[Any],
            survivors: list[Any],
            blocked: list[Any],
            errors: list[str],
        ) -> None:
            # Simulate a partial bisection write before the crash — this is
            # the state main()'s except block must undo.
            lock_path.write_bytes(b"lockfileVersion: mid-bisection\n")
            raise RuntimeError("check.sh subprocess vanished")

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

        # Nothing succeeded anywhere (nd_up_tagged got zeroed by the crash,
        # no Python updates), so main() exits 1 by its own "only fail loudly
        # when nothing succeeded" rule — the PR body write happens before
        # that exit check, so it's still there to assert on.
        with pytest.raises(SystemExit):
            deps_update.main()

        assert lock_path.read_bytes() == original_lock
        assert resync_calls == 1
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

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            if workspace == "frontend":
                (tmp_path / "frontend" / "package.json").write_bytes(
                    applied_frontend_pkg
                )
                return [("svelte", "5.0.0", "5.1.0", release_dt)], []
            # electron-ws: partially mutate the shared lockfile, then crash.
            lock_path.write_bytes(b"lockfileVersion: mid-crash\n")
            raise RuntimeError("pnpm add vanished mid-apply")

        def fake_verify_node(
            snapshots: dict[Path, bytes],
            batch: list[Any],
            survivors: list[Any],
            blocked: list[Any],
            errors: list[str],
        ) -> None:
            survivors.extend(batch)

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

    def test_update_python_exception_restores_uv_lock_without_verify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """update_python() itself applies packages one at a time regardless
        of --verify — the snapshot _guard() restores from must exist whether
        or not --verify was passed, or a crash on this path leaves uv.lock
        holding an unreported mutation the PR body claims never happened.
        This is the gap --verify used to gate the snapshot capture on.
        """
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

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            uv_lock_path.write_bytes(b"lockfileVersion: mid-loop\n")
            raise RuntimeError("uv lock --upgrade-package vanished")

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        pr_body_file = tmp_path / "pr-body.md"

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(
            sys, "argv", ["deps_update.py", "--pr-body-file", str(pr_body_file)]
        )

        with pytest.raises(SystemExit):
            deps_update.main()  # no --verify — the flag under test

        assert uv_lock_path.read_bytes() == original_lock
        assert "update_python crashed unexpectedly" in pr_body_file.read_text()

    def test_update_node_exception_resyncs_without_verify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirrors the test above for the Node side: a crash in one
        workspace's update_node() call must restore pnpm-lock.yaml and
        resync node_modules even when --verify was never passed, since
        --verify only ever gated whether check.sh bisection runs — never
        whether main() has something to restore to on a crash.
        """
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

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            lock_path.write_bytes(b"lockfileVersion: mid-crash\n")
            raise RuntimeError("pnpm add vanished mid-apply")

        resync_calls = 0

        def fake_resync() -> bool:
            nonlocal resync_calls
            resync_calls += 1
            return True

        pr_body_file = tmp_path / "pr-body.md"

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(
            sys, "argv", ["deps_update.py", "--pr-body-file", str(pr_body_file)]
        )

        with pytest.raises(SystemExit):
            deps_update.main()  # no --verify — the flag under test

        assert lock_path.read_bytes() == original_lock
        assert resync_calls == len(deps_update._WORKSPACE_DIR)
        assert "update_node crashed unexpectedly" in pr_body_file.read_text()

    def test_update_node_resync_hook_exception_does_not_crash_main(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_guard()'s on_restore hook is itself wrapped in a try/except — if
        _resync_node_modules() raises (not just returns False) after a
        workspace crash, main() must record it and keep going, not crash
        uncaught before the PR body is ever written. This is the exact bug
        class an unguarded resync call sitting right next to a _guard() call
        used to reproduce: correctness insurance for one crash path, with a
        second, unguarded crash path one line below it.
        """
        for name in deps_update._WORKSPACE_DIR.values():
            pkg = (
                tmp_path / name / "package.json"
                if name != "."
                else tmp_path / "package.json"
            )
            pkg.parent.mkdir(parents=True, exist_ok=True)
            pkg.write_text("{}")
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "uv.lock").write_text("")
        (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n")

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            raise RuntimeError("pnpm outdated returned garbage")

        def fake_resync() -> bool:
            raise RuntimeError("pnpm vanished from PATH")

        pr_body_file = tmp_path / "pr-body.md"

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "update_node", fake_update_node)
        monkeypatch.setattr(deps_update, "_resync_node_modules", fake_resync)
        monkeypatch.setattr(
            sys, "argv", ["deps_update.py", "--pr-body-file", str(pr_body_file)]
        )

        with pytest.raises(SystemExit):
            deps_update.main()  # must reach here, not crash on the hook's own raise

        body = pr_body_file.read_text()
        assert "update_node crashed unexpectedly" in body
        assert "update_node restore hook crashed unexpectedly" in body

    def test_verify_node_resync_hook_exception_does_not_crash_main(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirrors the test above for _verify_node's on_restore hook."""
        release_dt = datetime.now(UTC) - timedelta(days=30)

        for name in deps_update._WORKSPACE_DIR.values():
            pkg = (
                tmp_path / name / "package.json"
                if name != "."
                else tmp_path / "package.json"
            )
            pkg.parent.mkdir(parents=True, exist_ok=True)
            pkg.write_text("{}")
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "uv.lock").write_text("")
        (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n")

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        def fake_update_node(
            cooldown: int,
            workspace: str,
            errors: list[str],
            registry_cache: dict[str, Any] | None = None,
        ) -> tuple[list[Any], list[Any]]:
            if workspace == "frontend":
                return [("svelte", "5.0.0", "5.1.0", release_dt)], []
            return [], []

        def fake_verify_node(
            snapshots: dict[Path, bytes],
            batch: list[Any],
            survivors: list[Any],
            blocked: list[Any],
            errors: list[str],
        ) -> None:
            raise RuntimeError("check.sh subprocess vanished")

        def fake_resync() -> bool:
            raise RuntimeError("pnpm vanished from PATH")

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

        with pytest.raises(SystemExit):
            deps_update.main()  # must reach here, not crash on the hook's own raise

        body = pr_body_file.read_text()
        assert "_verify_node crashed unexpectedly" in body
        assert "_verify_node restore hook crashed unexpectedly" in body


class TestErrorsSurviveMidLoopCrash:
    """A prior version of update_python()/update_node() built `errors`
    locally and only returned it at the very end of the per-package loop —
    an unhandled exception partway through the loop discarded every error
    already recorded for packages processed before the one that crashed,
    along with the crash itself. `errors` is now a caller-owned list
    mutated in place, so whatever was recorded before the crash survives it
    (and is still there for _guard()'s crash message to join alongside).
    """

    def test_update_python_preserves_errors_recorded_before_a_later_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text(
            '[project]\ndependencies = ["pkg-a==1.0.0", "pkg-b==1.0.0"]\n'
        )
        (backend / "uv.lock").write_text(
            '[[package]]\nname = "pkg-a"\nversion = "1.0.0"\n'
            '[[package]]\nname = "pkg-b"\nversion = "1.0.0"\n'
        )
        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        old = datetime.now(UTC) - timedelta(days=30)

        def fake_fetch_json(url: str, *, headers: Any = None) -> dict[str, Any] | None:
            if "pkg-a" in url:
                return {"info": {"version": "2.0.0"}, "releases": {}}
            # Simulates an unexpected exception type _fetch_json's own
            # except clause doesn't already handle — not a registry error.
            raise ValueError("registry client bug")

        def fake_apply(name: str, backend: Path, errors: list[str]) -> tuple[bool, str]:
            return False, "pkg-a failed to apply"

        def fake_release_date(
            name: str, version: str, all_releases: dict[str, Any]
        ) -> datetime | None:
            return old

        monkeypatch.setattr(deps_update, "_fetch_json", fake_fetch_json)
        monkeypatch.setattr(deps_update, "_apply_python_package", fake_apply)
        monkeypatch.setattr(deps_update, "_pypi_release_date", fake_release_date)

        errors: list[str] = []
        with pytest.raises(ValueError, match="registry client bug"):
            deps_update.update_python(10, errors)

        assert any("pkg-a failed to apply" in e for e in errors)

    def test_update_node_preserves_errors_recorded_before_a_later_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = datetime.now(UTC) - timedelta(days=30)

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            stdout = json.dumps(
                {
                    "pkg-a": {"current": "1.0.0", "latest": "2.0.0"},
                    "pkg-b": {"current": "1.0.0", "latest": "2.0.0"},
                }
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        def fake_registry_data(
            name: str, cache: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            if name == "pkg-a":
                return {"time": {"2.0.0": old.isoformat()}}
            # Simulates an unexpected exception mid-loop, not a normal
            # unreachable-registry failure (which returns None, not raises).
            raise ValueError("registry client bug")

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
            return False, "pkg-a failed to apply"

        monkeypatch.setattr(deps_update, "_run", fake_run)
        monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)
        monkeypatch.setattr(deps_update, "_resync_node_modules", lambda: True)

        errors: list[str] = []
        with pytest.raises(ValueError, match="registry client bug"):
            deps_update.update_node(10, "frontend", errors)

        assert any("pkg-a failed to apply" in e for e in errors)

    def test_update_node_crash_and_prior_error_both_reach_the_pr_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end version of the test above: through main() itself
        (real update_node(), real _guard()), not just checking the list
        the two functions above assert on directly — the CHANGELOG entry's
        actual promise is that both messages reach the committed PR body.
        """
        old = datetime.now(UTC) - timedelta(days=30)
        monkeypatch.setattr(deps_update, "_WORKSPACE_DIR", {"frontend": "frontend"})

        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text("{}")
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "uv.lock").write_text("")
        (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n")

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[Any], list[Any]]:
            return [], []

        def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            stdout = json.dumps(
                {
                    "pkg-a": {"current": "1.0.0", "latest": "2.0.0"},
                    "pkg-b": {"current": "1.0.0", "latest": "2.0.0"},
                }
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        def fake_registry_data(
            name: str, cache: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            if name == "pkg-a":
                return {"time": {"2.0.0": old.isoformat()}}
            raise ValueError("registry client bug")

        def fake_apply(
            workspace: str, name: str, version: str, errors: list[str]
        ) -> tuple[bool, str]:
            return False, "pkg-a failed to apply"

        pr_body_file = tmp_path / "pr-body.md"

        monkeypatch.setattr(deps_update, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(deps_update, "_run", fake_run)
        monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
        monkeypatch.setattr(deps_update, "_apply_node_package", fake_apply)
        monkeypatch.setattr(deps_update, "_resync_node_modules", lambda: True)
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--pr-body-file", str(pr_body_file)],
        )

        # Nothing succeeded anywhere in this run, so main() exits 1 by its
        # own "only fail loudly when nothing succeeded" rule — the PR body
        # write happens before that exit check, so it's still there to
        # assert on.
        with pytest.raises(SystemExit):
            deps_update.main()

        body = pr_body_file.read_text()
        assert "pkg-a failed to apply" in body
        assert "`frontend`: update_node crashed unexpectedly" in body
