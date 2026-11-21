"""Tests for scripts/deps_update.py — the updater vs. the package manager itself.

Reproduces analecta#162: a scheduled dependency-update run applied pnpm
itself as if it were an ordinary workspace devDependency — pnpm@12.21.0
landed as a root dependency in package.json and pnpm-lock.yaml on the
generated PR branch, and CI on that PR failed with ERR_PNPM_IGNORED_BUILDS
because pnpm's own install.js is deliberately not on the ignored-builds
allowlist.

Scan surface (observed reproduction): `pnpm outdated --json --filter
analecta` lists pnpm itself among the workspace's reported devDependencies
(`dependencyType: "devDependencies"`, `dependentPackages: ["analecta"]`),
12.20.0 -> 12.21.0, and the updater's apply path treated that entry like
any other dependency.

Invariants derived from the incident, asserted per observable surface:

1. Apply behavior — a scan entry for pnpm itself must never reach the
   generic Node update path (`pnpm add pnpm@<version> …`).
2. Generated manifests — package.json and pnpm-lock.yaml must not carry a
   pnpm bump produced by the updater (other packages' legitimate bumps may
   still change the files; only pnpm's own absence is asserted).
3. Generated PR body — pnpm must not be reported as an applied update in
   the Node table, while ordinary candidates from the same scan keep their
   usual rows.

The fixture simulates the incident faithfully: the workspace's package.json
mirrors the repo root's shape (scripts block ahead of devDependencies, a
packageManager pin, no pnpm devDependency), the scan reports pnpm exactly
as pnpm 12.20.0 reported it, and the fake `pnpm add --save-exact` mutates
the manifests the way real pnpm did — inserting pnpm@12.21.0 into
devDependencies and touching pnpm-lock.yaml.
"""

import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
import deps_update  # pyright: ignore[reportMissingImports]

# Mirrors the repo root's package.json shape (scripts block ahead of
# devDependencies, packageManager pin, pnpm itself NOT listed as a
# dependency) — the state from which the incident's `pnpm add pnpm@12.21.0`
# ADDED pnpm as a root dependency.
_ROOT_PACKAGE_JSON = (
    "{\n"
    '  "name": "analecta",\n'
    '  "version": "0.6.1",\n'
    '  "private": true,\n'
    '  "type": "module",\n'
    '  "scripts": {\n'
    '    "dist": "pnpm --filter frontend build"\n'
    "  },\n"
    '  "devDependencies": {\n'
    '    "typescript-eslint": "8.73.0"\n'
    "  },\n"
    '  "packageManager": "pnpm@12.20.0+sha512-AAAA"\n'
    "}\n"
)

_PNPM_LOCK = (
    "lockfileVersion: '9.0'\n"
    "\n"
    "importers:\n"
    "  .:\n"
    "    dependencies:\n"
    "      typescript-eslint:\n"
    "        specifier: 8.73.0\n"
    "        version: 8.73.0\n"
)


def _make_repo(tmp_path: Path) -> Path:
    """Materialize a minimal repo root with the incident's pre-run manifests."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "uv.lock").write_text("")
    (tmp_path / "package.json").write_text(_ROOT_PACKAGE_JSON)
    (tmp_path / "pnpm-lock.yaml").write_text(_PNPM_LOCK)
    return tmp_path


def _old_release() -> datetime:
    """A release date comfortably past any cooldown, so the age gate passes."""
    return datetime.now(UTC) - timedelta(days=30)


def _scan_and_fakes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, list[list[str]]]:
    """Wire the analecta#162 scan surface with faithful fakes.

    Returns (repo_root, add_calls). The root workspace's `pnpm outdated`
    scan reports pnpm itself (exactly as reproduced in the incident:
    devDependencies of workspace "analecta", 12.20.0 -> 12.21.0) alongside
    an ordinary devDependency (typescript-eslint) so the tests can assert
    the exclusion is pnpm-specific, not a workspace-wide abort. The fake
    `pnpm add --save-exact` reproduces the incident's on-disk effect: it
    rewrites package.json (inserting the added package into
    devDependencies) and appends a lockfile entry, the way real pnpm did
    for pnpm@12.21.0.
    """
    root = _make_repo(tmp_path)
    monkeypatch.setattr(deps_update, "REPO_ROOT", root)
    monkeypatch.setattr(deps_update, "_WORKSPACE_DIR", {"analecta": "."})

    release_dt = _old_release()
    outdated_json = json.dumps(
        {
            "analecta": {
                "pnpm": {
                    "current": "12.20.0",
                    "wanted": "12.21.0",
                    "latest": "12.21.0",
                    "dependencyType": "devDependencies",
                    "dependentPackages": ["analecta"],
                },
                "typescript-eslint": {
                    "current": "8.73.0",
                    "wanted": "8.74.0",
                    "latest": "8.74.0",
                    "dependencyType": "devDependencies",
                    "dependentPackages": ["analecta"],
                },
            }
        }
    )

    def fake_registry_data(
        name: str, cache: dict[str, dict[str, object]] | None = None
    ) -> dict[str, object] | None:
        version = "12.21.0" if name == "pnpm" else "8.74.0"
        return {"time": {version: release_dt.isoformat()}}

    pkg_path = root / "package.json"
    lock_path = root / "pnpm-lock.yaml"
    add_calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["pnpm", "outdated"]:
            return subprocess.CompletedProcess(cmd, 1, stdout=outdated_json, stderr="")
        if cmd[:2] == ["pnpm", "add"]:
            add_calls.append(cmd)
            name, version = cmd[2].rsplit("@", 1)
            text = pkg_path.read_text()
            if f'"{name}":' in text:
                # Existing specifier: real pnpm add rewrites it in place.
                text = re.sub(
                    rf'("{re.escape(name)}":\s*")[^"]*(")',
                    rf"\g<1>{version}\g<2>",
                    text,
                    count=1,
                )
            else:
                # New dependency: real pnpm add --save-exact inserts it into
                # devDependencies — the exact mechanism that landed
                # pnpm@12.21.0 as a root dependency in the incident.
                marker = '    "typescript-eslint": "8.73.0"\n'
                text = text.replace(
                    marker,
                    f'    "typescript-eslint": "8.73.0",\n    "{name}": "{version}"\n',
                )
            pkg_path.write_text(text)
            lock = lock_path.read_text()
            lock += f"  extra-importer-{name}:\n    specifier: {version}\n"
            lock_path.write_text(lock)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        # pnpm install (lockfile resync) / pnpm dedupe: succeed untouched.
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
    monkeypatch.setattr(deps_update, "_run", fake_run)
    return root, add_calls


class TestPnpmNeverReachesTheGenericApplyPath:
    """A scan entry for pnpm itself must not be applied as an ordinary
    dependency (analecta#162, symptom: pnpm@12.21.0 landed in package.json
    and pnpm-lock.yaml via the updater's generic apply path).
    """

    def test_scan_reporting_pnpm_never_issues_a_pnpm_add(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, add_calls = _scan_and_fakes(monkeypatch, tmp_path)

        errors: list[str] = []
        updated, skipped = deps_update.update_node(10, "analecta", errors)

        # The mechanism: the generic Node update path must never run
        # `pnpm add pnpm@<version>` for the package manager itself.
        pnpm_adds = [cmd for cmd in add_calls if cmd[2].startswith("pnpm@")]
        assert pnpm_adds == []
        # The exclusion must be pnpm-specific, not a workspace-wide abort:
        # the ordinary devDependency reported by the very same scan still
        # goes through the generic path exactly as before.
        assert [cmd[2] for cmd in add_calls] == ["typescript-eslint@8.74.0"]
        assert [name for name, *_rest in updated] == ["typescript-eslint"]
        assert [name for name, *_rest in skipped] == []
        assert errors == []
        _ = root

    def test_pnpm_itself_never_lands_in_the_generated_manifests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, add_calls = _scan_and_fakes(monkeypatch, tmp_path)
        pkg_path = root / "package.json"
        lock_path = root / "pnpm-lock.yaml"

        errors: list[str] = []
        deps_update.update_node(10, "analecta", errors)

        pkg_after = pkg_path.read_text()
        lock_after = lock_path.read_text()

        # The exact incident surface: pnpm@12.21.0 landed as a root
        # dependency in package.json and pnpm-lock.yaml. Ordinary packages'
        # legitimate bumps may change the files — only pnpm's own presence
        # is asserted against.
        assert '"pnpm"' not in pkg_after
        assert "pnpm" not in lock_after
        assert not [cmd for cmd in add_calls if cmd[2].startswith("pnpm@")]


class TestPrBodyNeverReportsPnpmAsApplied:
    """The generated PR body must not list pnpm itself among the applied
    Node updates (analecta#162: the updater's apply path treated the scan's
    pnpm entry like any other dependency, so the PR body reported it as an
    applied update).
    """

    def test_node_updated_table_omits_pnpm_but_keeps_ordinary_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, add_calls = _scan_and_fakes(monkeypatch, tmp_path)
        pr_body_file = tmp_path / "pr-body.md"

        def fake_update_python(
            cooldown: int, errors: list[str]
        ) -> tuple[list[object], list[object]]:
            return [], []

        monkeypatch.setattr(deps_update, "update_python", fake_update_python)
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--pr-body-file", str(pr_body_file)],
        )

        try:
            deps_update.main()
        except SystemExit as exc:  # tolerated: nothing-succeeded runs exit 1
            exit_code = exc.code
        else:
            exit_code = None

        body = pr_body_file.read_text()
        node_section = body.split("### Node / pnpm", 1)[1]
        # pnpm must not appear as an applied update in the Node table — the
        # incident's PR surface. (How the updater surfaces pnpm's own
        # existence — silently, skipped, or as a note/error — is not
        # asserted here; only that it is never an *applied* update.)
        assert "| `pnpm` |" not in node_section
        # The ordinary candidate from the same scan keeps its usual row:
        # the exclusion must not drop the workspace's real updates.
        assert "| `typescript-eslint` | `analecta` |" in node_section
        # And the generic apply path must not have run for pnpm through the
        # CLI entry point either.
        assert not [cmd for cmd in add_calls if cmd[2].startswith("pnpm@")]
        assert exit_code is None
        _ = root


# ---------------------------------------------------------------------------
# T2 — S2 (--include-pnpm toolchain bump), S4 (--help), and the advisor's
# three durable T1 regression pins (P1/P2/P3, folded in per L6).
#
# RED status at HEAD (pre-T2): `--include-pnpm` does not exist yet, so
# argparse rejects it with SystemExit(2) before any updater logic runs. The
# S2 tests catch that SystemExit and put their assertions on the missing
# mechanism itself (manifest states, subprocess invocations, PR body), so
# the observed red is the mechanism, not the argparse rejection.
# ---------------------------------------------------------------------------

_OLD_PIN = "12.20.0"
_NEW_PIN = "12.21.0"
# Deliberately unlike the old pin's sha (AAAA in _ROOT_PACKAGE_JSON) and
# unlike anything derivable from the version string: the recompute test
# asserts the exact registry dist.integrity value.
_NEW_SHA = "sha512-UkVHSVNUWUlfSU5URUdSSVRZX01PQ0tfVkFMVUU9PQ=="


def _pnpm_scan_json(workspace: str) -> str:
    """Build a pnpm-only `pnpm outdated --json` payload for *workspace*."""
    return json.dumps(
        {
            workspace: {
                "pnpm": {
                    "current": _OLD_PIN,
                    "wanted": _NEW_PIN,
                    "latest": _NEW_PIN,
                    "dependencyType": "devDependencies",
                    "dependentPackages": [workspace],
                }
            }
        }
    )


def _combined_scan_json(workspace: str) -> str:
    """Build a scan payload with pnpm plus one ordinary eligible update.

    Mirrors the combined-run composition the L11 pin drives: the SAME
    `pnpm outdated --json` scan that feeds the flag path also reports an
    ordinary devDependency (typescript-eslint, 8.73.0 -> 8.74.0) so the
    flagged run exercises both the generic apply path and the toolchain
    bump.
    """
    doc: dict[str, Any] = json.loads(_pnpm_scan_json(workspace))
    doc[workspace]["typescript-eslint"] = {
        "current": "8.73.0",
        "wanted": "8.74.0",
        "latest": "8.74.0",
        "dependencyType": "devDependencies",
        "dependentPackages": [workspace],
    }
    return json.dumps(doc)


def _make_repo_with_toolchain(tmp_path: Path) -> Path:
    """Extend the incident repo with the other two toolchain manifests.

    All three manifests pin pnpm at _OLD_PIN, mirroring the real repo's
    cross-manifest sync (package.json packageManager, pnpm-workspace.yaml
    configDependencies, .mise.toml tools.pnpm).
    """
    root = _make_repo(tmp_path)
    (root / "pnpm-workspace.yaml").write_text(
        "minimumReleaseAge: 5760\n"
        "\n"
        "packages:\n"
        "  - 'frontend'\n"
        "  - 'electron'\n"
        "\n"
        "# keep in sync with .mise.toml and package.json packageManager\n"
        "configDependencies:\n"
        f"  pnpm: {_OLD_PIN}\n"
        "\n"
        "overrides:\n"
        "  tmp: '0.2.7'\n"
    )
    (root / ".mise.toml").write_text(
        "[tools]\n"
        'python = "3.15"\n'
        'node   = "26.13.1"\n'
        f'pnpm   = "{_OLD_PIN}"\n'
        'uv     = "latest"\n'
    )
    return root


def _toolchain_pins(root: Path) -> tuple[Any, Any, Any]:
    """Read the pnpm pin from all three toolchain manifests.

    Returns (packageManager string, configDependencies pnpm, .mise.toml
    tools.pnpm).
    """
    pkg: dict[str, Any] = json.loads((root / "package.json").read_text())
    ws: dict[str, Any] = yaml.safe_load((root / "pnpm-workspace.yaml").read_text())
    with (root / ".mise.toml").open("rb") as f:
        mise: dict[str, Any] = tomllib.load(f)
    return (
        pkg["packageManager"],
        ws["configDependencies"]["pnpm"],
        mise["tools"]["pnpm"],
    )


def _is_install_typed(cmd: list[str]) -> bool:
    """Does *cmd* invoke an install at all (any pnpm binary, any mechanism)?

    The verification observable the tests pin is ordering-based and
    mechanism-agnostic (L7 ratification): the new-pnpm switch is achieved
    via the bumped configDependencies (pnpm self-manages its env from that
    key), so requiring the new version in argv would force an unnatural
    implementation. What must hold (L8, strict ordering): the ONLY
    install-typed cwd==repo-root invocations on the flag path are the
    verification ones, and every one of them happens only after the three
    manifests already carry the new pin.
    """
    return "install" in cmd


def _pr_sections(body: str) -> list[tuple[str, str]]:
    """Split a PR body on top-level `###` headings.

    Returns (heading, section_text) pairs, the heading without its `### `
    marker. Text before the first `###` (the preamble) is not a section
    and is not returned.
    """
    sections: list[tuple[str, str]] = []
    for chunk in re.split(r"(?m)^###\s+", body)[1:]:
        lines = chunk.splitlines()
        heading = lines[0].strip() if lines else ""
        text = "\n".join(lines[1:]) if len(lines) > 1 else ""
        sections.append((heading, text))
    return sections


def _toolchain_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    verify_ok: bool = True,
    scan_json: str | None = None,
    ordinary_registry: dict[str, Any] | None = None,
    add_leaves_range: bool = False,
) -> tuple[Path, list[list[str]], list[tuple[Path, tuple[Any, Any, Any]]]]:
    """Wire the repo with a pnpm-only scan plus fakes for the bump path.

    The three keyword-only knobs default to the pnpm-only composition the
    existing tests pin; the combined-run test (L11 decision 3) turns them
    on: *scan_json* replaces the pnpm-only scan payload, *ordinary_registry*
    is the registry document returned for non-pnpm names (the ordinary
    candidate's release-date data), and *add_leaves_range* makes the fake
    `pnpm add` leave a range specifier in package.json — the observed
    --save-exact behavior — so the ordinary apply path drives its own
    exact-pin resync install at the repo root through the real code path.

    Returns (root, run_calls, install_states): *run_calls* records every
    _run argv; *install_states* records, at the moment each install-typed
    invocation is made, its cwd plus the three manifests' pnpm pins — so a
    test can assert the verification ran at the repo root against the
    already-bumped manifests (call-order observable, L7; strict ordering
    L8: any pre-bump root install is a failure, not tolerated by default).
    """
    root = _make_repo_with_toolchain(tmp_path)
    outdated_json = scan_json if scan_json is not None else _pnpm_scan_json("analecta")
    run_calls: list[list[str]] = []
    install_states: list[tuple[Path, tuple[Any, Any, Any]]] = []

    def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        run_calls.append(list(cmd))
        if "outdated" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout=outdated_json, stderr="")
        if cmd[:2] == ["pnpm", "add"]:
            if add_leaves_range:
                # Real pnpm's --save-exact does not reliably strip the range
                # operator when rewriting an existing specifier (observed;
                # see _ensure_exact_specifier's docstring) — reproduce that
                # on-disk effect so _ensure_exact_specifier finds a range,
                # rewrites it to the exact pin, and issues its lockfile
                # resync install at the repo root.
                name, version = cmd[2].rsplit("@", 1)
                pkg_path = root / "package.json"
                text = pkg_path.read_text()
                _ = pkg_path.write_text(
                    re.sub(
                        rf'("{re.escape(name)}":\s*")[^"]*(")',
                        rf"\g<1>^{version}\g<2>",
                        text,
                        count=1,
                    )
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if _is_install_typed(cmd):
            install_states.append((cwd, _toolchain_pins(root)))
            if verify_ok:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr=(
                    "ERR_PNPM_IGNORED_BUILDS: the following dependencies have"
                    " not been approved for builds and cannot be installed"
                ),
            )
        # Any other pnpm call (dedupe, resync, …) succeeds untouched.
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    registry_doc: dict[str, Any] = {
        "time": {_NEW_PIN: _old_release().isoformat()},
        "versions": {_NEW_PIN: {"dist": {"integrity": _NEW_SHA}}},
    }

    def fake_registry_data(
        name: str, cache: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        if name == "pnpm":
            return dict(registry_doc)
        if ordinary_registry is not None:
            return dict(ordinary_registry)
        return None

    def fake_fetch_json(
        url: str, *, headers: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        if url.rstrip("/").endswith("registry.npmjs.org/pnpm/12.21.0"):
            return dict(registry_doc["versions"][_NEW_PIN])
        if "registry.npmjs.org/pnpm" in url:
            return dict(registry_doc)
        return None

    def fake_run_check(repo_root: Path, scope: str) -> bool:
        return True

    def fake_update_python(
        cooldown: int, errors: list[str]
    ) -> tuple[list[object], list[object]]:
        return [], []

    monkeypatch.setattr(deps_update, "REPO_ROOT", root)
    monkeypatch.setattr(deps_update, "_WORKSPACE_DIR", {"analecta": "."})
    monkeypatch.setattr(deps_update, "_run", fake_run)
    monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
    monkeypatch.setattr(deps_update, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
    monkeypatch.setattr(deps_update, "update_python", fake_update_python)
    return root, run_calls, install_states


def _wire_pnpm_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    workspaces: dict[str, str],
    scans: dict[str, str],
) -> tuple[Path, list[list[str]]]:
    """Wire main()-level fakes for Node scans keyed by workspace.

    *workspaces* replaces _WORKSPACE_DIR (a minimal package.json is
    materialized for each); *scans* maps workspace name -> the raw
    `pnpm outdated --json` stdout the fake returns for that workspace's
    --filter (mutable so a test can swap payloads between runs). Returns
    (root, add_calls).
    """
    root = _make_repo(tmp_path)
    for name, rel_dir in workspaces.items():
        pkg_dir = root / rel_dir
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "package.json").write_text(json.dumps({"name": name}))
    add_calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["pnpm", "outdated"]:
            workspace = cmd[cmd.index("--filter") + 1]
            return subprocess.CompletedProcess(
                cmd, 1, stdout=scans.get(workspace, "{}"), stderr=""
            )
        if cmd[:2] == ["pnpm", "add"]:
            add_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_registry_data(
        name: str, cache: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        # pnpm never reaches the registry on the excluded path (T1), and the
        # pins' scans are pnpm-only — a fetch here is a test bug.
        raise AssertionError(f"unexpected registry fetch: {name}")

    def fake_run_check(repo_root: Path, scope: str) -> bool:
        return True

    def fake_update_python(
        cooldown: int, errors: list[str]
    ) -> tuple[list[object], list[object]]:
        return [], []

    monkeypatch.setattr(deps_update, "REPO_ROOT", root)
    monkeypatch.setattr(deps_update, "_WORKSPACE_DIR", workspaces)
    monkeypatch.setattr(deps_update, "_run", fake_run)
    monkeypatch.setattr(deps_update, "_npm_registry_data", fake_registry_data)
    monkeypatch.setattr(deps_update, "_run_check", fake_run_check)
    monkeypatch.setattr(deps_update, "update_python", fake_update_python)
    return root, add_calls


class TestIncludePnpmToolchainBump:
    """S2: an explicit --include-pnpm run bumps the pnpm toolchain itself.

    The bump updates all three manifests atomically (packageManager with a
    sha512 recomputed from the registry's dist.integrity,
    pnpm-workspace.yaml configDependencies, .mise.toml), then verifies: the
    verification is an install-typed invocation at the repo root, and the
    ONLY install-typed root invocations are the verification ones, all run
    after the manifests carry the new pin (L7 ordering-based mechanism,
    L8 strict ordering). The scheduled path (no flag) still never touches
    the toolchain — that boundary is T1's behavior.
    """

    def test_flag_updates_all_three_toolchain_manifests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One flagged run updates all three manifests — none left stale."""
        root, _run_calls, _states = _toolchain_fakes(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["deps_update.py", "--include-pnpm"])

        exit_code: int | str | None = None
        try:
            deps_update.main()
        except SystemExit as exc:
            exit_code = exc.code

        pkg_manager, config_dep, mise_pnpm = _toolchain_pins(root)
        # The mechanism: the explicit flag flips pnpm itself from "excluded"
        # to "bumped", across ALL THREE manifests in the same run.
        assert _NEW_PIN in str(pkg_manager)
        assert config_dep == _NEW_PIN
        assert mise_pnpm == _NEW_PIN
        # None left stale: the old pin is gone from every one of the three.
        assert _OLD_PIN not in (root / "package.json").read_text()
        assert _OLD_PIN not in (root / "pnpm-workspace.yaml").read_text()
        assert _OLD_PIN not in (root / ".mise.toml").read_text()
        assert exit_code is None

    def test_package_manager_sha512_is_recomputed_from_registry_integrity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The new packageManager pin carries the sha512 the registry reports
        for the new version (dist.integrity) — not one copied from the old
        pin or derivable from the version string.
        """
        root, _run_calls, _states = _toolchain_fakes(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["deps_update.py", "--include-pnpm"])

        exit_code: int | str | None = None
        try:
            deps_update.main()
        except SystemExit as exc:
            exit_code = exc.code

        pkg: dict[str, Any] = json.loads((root / "package.json").read_text())
        # The exact registry dist.integrity value lands in the pin...
        assert pkg["packageManager"] == f"pnpm@{_NEW_PIN}+{_NEW_SHA}"
        # ...not the old pin's sha.
        assert "sha512-AAAA" not in str(pkg["packageManager"])
        assert exit_code is None

    def test_verification_installs_run_only_after_the_bump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Strict ordering (L8/B1): the ONLY install-typed cwd==repo-root
        invocations on the flag path are the verification ones, and every
        one of them runs when all three manifests already carry the new
        pin. Asserted in BOTH directions: at least one post-bump
        verification install exists, and NO install-typed cwd-root
        invocation was recorded with pre-bump pins — a pre-bump resync
        install would fail here; if a future implementation legitimately
        needs one, that is a design change discussed at the gate, not
        tolerated by default. (Mechanism still L7: ordering-based,
        no argv-version requirement — the new-pnpm switch is achieved via
        the bumped configDependencies, so an old-pnpm install against the
        new configDependencies reproduces the mirrored
        ERR_PNPM_IGNORED_BUILDS failure the design exists to avoid.)
        """
        root, _run_calls, install_states = _toolchain_fakes(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["deps_update.py", "--include-pnpm"])

        try:
            deps_update.main()
        except SystemExit:
            # Pre-T2: unrecognized flag; the asserts below carry the red.
            pass

        # The fakes snapshot the three manifests' pins at call time, so
        # each recorded state is exactly what the implementation saw on
        # disk when it issued the invocation.
        root_installs = [(cwd, pins) for cwd, pins in install_states if cwd == root]

        # Direction (i): at least one post-bump verification install must
        # exist — verification is intrinsic to the flag (L7), so a flag
        # path with no root install never verified.
        assert root_installs != [], (
            "no install-typed invocation ran at the repo root — the"
            " --include-pnpm path never verified with the new pnpm"
        )

        # Direction (ii): strict ordering — NO install-typed cwd-root
        # invocation may run with pre-bump pins; the only root installs
        # are the verification ones (L8).
        pre_bump = [
            pins
            for _cwd, pins in root_installs
            if _NEW_PIN not in str(pins[0])
            or pins[1] != _NEW_PIN
            or pins[2] != _NEW_PIN
        ]
        assert pre_bump == [], (
            "install-typed cwd-root invocation(s) ran before all three"
            f" manifests carried the new pin: {pre_bump!r} — a pre-bump"
            " install is a design change to discuss at the gate, not"
            " something the flag path may do by default"
        )

    def test_failing_new_pnpm_verification_fails_the_bump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing verification (the mirrored ERR_PNPM_IGNORED_BUILDS) must
        fail the bump: no manifest left at the new pin, the failure recorded
        for the PR body, and the run exiting non-zero.
        """
        root, _run_calls, _states = _toolchain_fakes(
            monkeypatch, tmp_path, verify_ok=False
        )
        pr_body_file = tmp_path / "pr-body.md"
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--include-pnpm", "--pr-body-file", str(pr_body_file)],
        )

        exit_code: int | str | None = None
        try:
            deps_update.main()
        except SystemExit as exc:
            exit_code = exc.code

        if not pr_body_file.exists():
            pytest.fail(
                "the --include-pnpm run never produced a PR body — the flag"
                " path did not run (pre-T2 the flag is unrecognized)"
            )
        body = pr_body_file.read_text()

        # The mechanism: the failed verification must fail the BUMP — all
        # three manifests back at the old pin, none left half-bumped.
        pkg_manager, config_dep, mise_pnpm = _toolchain_pins(root)
        assert str(pkg_manager) == f"pnpm@{_OLD_PIN}+sha512-AAAA"
        assert config_dep == _OLD_PIN
        assert mise_pnpm == _OLD_PIN
        # The failure reaches the PR body and the exit code.
        assert "ERR_PNPM_IGNORED_BUILDS" in body
        assert exit_code == 1

    def test_scheduled_path_without_the_flag_never_touches_the_toolchain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --include-pnpm, the same pnpm-only scan leaves all three
        toolchain manifests untouched (T1's exclusion, asserted here against
        the manifests rather than only the PR body).
        """
        root, _run_calls, _states = _toolchain_fakes(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["deps_update.py"])

        deps_update.main()

        pkg_manager, config_dep, mise_pnpm = _toolchain_pins(root)
        assert str(pkg_manager) == f"pnpm@{_OLD_PIN}+sha512-AAAA"
        assert config_dep == _OLD_PIN
        assert mise_pnpm == _OLD_PIN


class TestFlagPathPrBodyRendersToolchainSection:
    """S5: on the flag path the PR body renders the toolchain bump in its
    own dedicated top-level `###` section (B2: the section structure is
    asserted — heading neither `### Node` nor `### Python`, wording
    otherwise free, B4) — old -> new version, the three manifests touched,
    and the verification outcome naming the version that verified (B3) —
    while pnpm never appears as a row in the applied-bump table (the
    S1/S3 boundary holds even on the flag path).
    """

    def test_pr_body_dedicated_toolchain_section_and_no_applied_pnpm_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flag-path PR body carries the toolchain bump as its own
        apartado: a dedicated top-level `###` section whose heading is
        neither Node nor Python holds the old -> new transition, the three
        manifest names, and the verification outcome naming the new
        version; the applied Node section has no pnpm row.
        """
        _root, _run_calls, _states = _toolchain_fakes(monkeypatch, tmp_path)
        pr_body_file = tmp_path / "pr-body.md"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "deps_update.py",
                "--include-pnpm",
                "--pr-body-file",
                str(pr_body_file),
            ],
        )

        try:
            deps_update.main()
        except SystemExit:
            pass

        if not pr_body_file.exists():
            pytest.fail(
                "the --include-pnpm run never produced a PR body — the flag"
                " path did not run (pre-T2 the flag is unrecognized)"
            )
        body = pr_body_file.read_text()
        sections = _pr_sections(body)

        # B2: the toolchain observables live in a dedicated top-level
        # `###` section — a section whose heading is neither `### Node`
        # nor `### Python` (wording otherwise free, B4) must carry ALL of
        # them: the old -> new version transition, the three manifests the
        # bump touches, and the verification outcome.
        toolchain_sections = [
            (heading, text)
            for heading, text in sections
            if not heading.startswith("Node") and not heading.startswith("Python")
        ]
        transition = re.compile(
            rf"{re.escape(_OLD_PIN)}\s*(?:->|→)\s*{re.escape(_NEW_PIN)}"
        )
        matching = [
            (heading, text)
            for heading, text in toolchain_sections
            if transition.search(text) is not None
            and "packageManager" in text
            and "pnpm-workspace.yaml" in text
            and ".mise.toml" in text
            and re.search(r"verif", text, re.IGNORECASE) is not None
        ]
        assert len(matching) == 1, (
            "expected exactly one dedicated top-level `###` section"
            " (heading neither Node nor Python) carrying the toolchain"
            f" observables; sections seen:"
            f" {[heading for heading, _ in sections]!r}"
        )

        # B3 (owner-tightened L9): the verification-outcome LINE itself
        # names the version that verified — every line in the toolchain
        # section that mentions verification carries the new pin. (The
        # failure path's outcome stays covered by the rollback test's
        # ERR assertion.)
        _heading, toolchain_text = matching[0]
        verif_lines = [
            line
            for line in toolchain_text.splitlines()
            if re.search(r"verif", line, re.IGNORECASE) is not None
        ]
        assert verif_lines, "the toolchain section has no verification-outcome line"
        for line in verif_lines:
            assert _NEW_PIN in line, (
                f"verification-outcome line does not name the version"
                f" that verified ({_NEW_PIN}): {line!r}"
            )

        # The S1/S3 boundary on the flag path (kept from v2): the applied
        # `### Node` section must contain NO pnpm row — the bump lives in
        # the dedicated section, never as an applied update.
        node_sections = [
            text for heading, text in sections if heading.startswith("Node")
        ]
        assert node_sections != [], "the PR body has no `### Node` section"
        for node_text in node_sections:
            assert "| `pnpm` |" not in node_text


class TestHelpDocumentsIncludePnpm:
    """S4 (housekeeping): `--help` documents --include-pnpm's semantics,
    not just its name.
    """

    def test_help_documents_the_flag_and_its_semantics(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The flag's help entry names the three manifests it bumps and the
        new-pnpm verification, so `--help` alone explains the mechanism.
        """
        monkeypatch.setattr(sys, "argv", ["deps_update.py", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            deps_update.main()
        assert exc_info.value.code == 0

        help_text = capsys.readouterr().out
        # The flag itself...
        assert "--include-pnpm" in help_text
        # ...its semantics: the three manifests the bump covers...
        assert "packageManager" in help_text
        assert "pnpm-workspace" in help_text
        assert ".mise" in help_text
        # ...and the verification with the new pnpm.
        assert "verify" in help_text


class TestAdvisorRegressionPins:
    """The advisor's three durable T1 regression pins (folded into T2 per
    L6). These pin already-implemented behavior — expected GREEN at HEAD.
    """

    def test_p1_pnpm_only_scan_applies_nothing_and_reports_excluded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Boundary: a scan reporting ONLY pnpm issues no pnpm add, exits 0
        (an all-excluded run is not a failure), and the PR body carries the
        Excluded note next to `_No updates applied._` — never a pnpm row.
        """
        scans = {"analecta": _pnpm_scan_json("analecta")}
        _root, add_calls = _wire_pnpm_scan(
            monkeypatch, tmp_path, {"analecta": "."}, scans
        )
        pr_body_file = tmp_path / "pr-body.md"
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--pr-body-file", str(pr_body_file)],
        )

        deps_update.main()  # returning normally == exit code 0

        node_section = pr_body_file.read_text().split("### Node / pnpm", 1)[1]
        assert "_No updates applied._" in node_section
        assert "**Excluded" in node_section
        assert "| `pnpm` |" not in node_section
        assert add_calls == []

    def test_p2_pnpm_exclusion_deduped_across_workspaces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same toolchain entry reported by two workspaces renders the
        Excluded note exactly once in the Node section.
        """
        scans = {
            "frontend": _pnpm_scan_json("frontend"),
            "analecta": _pnpm_scan_json("analecta"),
        }
        workspaces = {"frontend": "frontend", "analecta": "."}
        _root, add_calls = _wire_pnpm_scan(monkeypatch, tmp_path, workspaces, scans)
        pr_body_file = tmp_path / "pr-body.md"
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--pr-body-file", str(pr_body_file)],
        )

        deps_update.main()

        node_section = pr_body_file.read_text().split("### Node / pnpm", 1)[1]
        assert node_section.count("**Excluded") == 1
        assert add_calls == []

    def test_p3_exclusion_accumulator_cleared_across_main_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pnpm exclusion recorded in run A must not leak into run B's PR
        body when the same process later runs with a quiet scan.
        """
        scans = {"analecta": _pnpm_scan_json("analecta")}
        _root, add_calls = _wire_pnpm_scan(
            monkeypatch, tmp_path, {"analecta": "."}, scans
        )
        body_a = tmp_path / "pr-body-a.md"
        body_b = tmp_path / "pr-body-b.md"

        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--pr-body-file", str(body_a)],
        )
        deps_update.main()
        assert "**Excluded" in body_a.read_text()

        # Run B: the same scan surface goes quiet.
        scans["analecta"] = "{}"
        monkeypatch.setattr(
            sys,
            "argv",
            ["deps_update.py", "--pr-body-file", str(body_b)],
        )
        deps_update.main()

        node_section_b = body_b.read_text().split("### Node / pnpm", 1)[1]
        assert "**Excluded" not in node_section_b
        assert "_No updates applied._" in node_section_b
        assert add_calls == []


# ---------------------------------------------------------------------------
# T2 addendum (L11) — anchoring regression tests for the three manifest
# rewriters, and the combined flag+ordinary-update run pin.
#
# Anchoring (advisor advisories A2/B1/A3 from L10, owner-ratified in L11):
# each rewriter must rewrite ONLY the canonical pin; a same-named key it
# must not touch — an `[env] pnpm = ...` key in .mise.toml (B1), a nested
# configDependencies -> other -> pnpm map (A2), or a shape it does not
# recognize (A3 and friends) — must make it return False with the file
# byte-identical. Tests 1-2 are RED at HEAD: the current regexes misfire
# onto those keys and would report a verified bump with the canonical pin
# left stale.
#
# Combined run (L11 decision 3): the L8 strict rule binds the TOOLCHAIN
# STEP — the ordinary apply path's own install-typed cwd-root calls (e.g.
# _ensure_exact_specifier's lockfile resync install) may legitimately run
# pre-bump and must not abort the toolchain bump.
# ---------------------------------------------------------------------------


class TestRewriterAnchoring:
    """Each manifest rewriter anchors on the canonical pin's exact location.

    A same-named key in a sibling/earlier/nested position must never be
    rewritten: a misfire reports a verified bump while the canonical pin
    stays stale (advisor B1/A2/A3, L10; owner-ratified RED-pin fix, L11).
    Byte-identity of the untouched file is asserted alongside the boolean —
    a rewriter that "rejects" but mutates is still broken.
    """

    def test_mise_rewriter_rejects_a_second_pnpm_key_outside_tools(
        self, tmp_path: Path
    ) -> None:
        """B1: an `[env] pnpm = "9.0.0"` key BEFORE the [tools] table must be
        ignored — the rewriter pins [tools] pnpm only. RED at HEAD: the
        line-anchored regex rewrites the first `pnpm =` line it sees (the
        [env] key) and reports success with the canonical pin stale.
        """
        text = '[env]\npnpm = "9.0.0"\n\n[tools]\npnpm   = "12.20.0"\n'
        path = tmp_path / ".mise.toml"
        _ = path.write_text(text)

        assert deps_update._bump_mise_pnpm(path, _NEW_PIN) is False
        assert path.read_text() == text

    def test_workspace_rewriter_rejects_nested_pnpm_key(self, tmp_path: Path) -> None:
        """A2: a pnpm key nested under configDependencies -> other is not
        the toolchain pin. RED at HEAD: the lazy indented-lines group walks
        down into the nested map and rewrites `other.pnpm` instead of
        rejecting the shape.
        """
        text = "configDependencies:\n  other:\n    pnpm: 1.2.3\n  pnpm: 12.20.0\n"
        path = tmp_path / "pnpm-workspace.yaml"
        _ = path.write_text(text)

        assert deps_update._bump_workspace_config_dependency(path, _NEW_PIN) is False
        assert path.read_text() == text

    def test_earlier_block_pnpm_key_is_ignored(self, tmp_path: Path) -> None:
        """A same-named key in an earlier block (allowBuilds.pnpm) is not
        the pin: the flat configDependencies pin is rewritten and the
        earlier block's `pnpm: true` survives untouched.
        """
        text = (
            f"allowBuilds:\n  pnpm: true\n\nconfigDependencies:\n  pnpm: {_OLD_PIN}\n"
        )
        path = tmp_path / "pnpm-workspace.yaml"
        _ = path.write_text(text)

        assert deps_update._bump_workspace_config_dependency(path, _NEW_PIN) is True
        after = path.read_text()
        assert "pnpm: true" in after
        assert f"pnpm: {_NEW_PIN}" in after
        assert f"pnpm: {_OLD_PIN}" not in after

    @pytest.mark.parametrize(
        ("rewriter", "manifest_text", "new_value"),
        [
            pytest.param(
                deps_update._bump_mise_pnpm,
                "[tools]\npnpm = '12.20.0'\n",
                _NEW_PIN,
                id="mise-single-quoted-value",
            ),
            pytest.param(
                deps_update._bump_package_manager,
                '{\n  "packageManager": "yarn@4.0.0"\n}\n',
                f"pnpm@{_NEW_PIN}+{_NEW_SHA}",
                id="non-pnpm-package-manager",
            ),
            pytest.param(
                deps_update._bump_workspace_config_dependency,
                "configDependencies:\noverrides:\n  pnpm: 12.20.0\n",
                _NEW_PIN,
                id="discontiguous-config-dependencies",
            ),
        ],
    )
    def test_unrecognized_shapes_abort_without_touching_the_file(
        self,
        tmp_path: Path,
        rewriter: Callable[[Path, str], bool],
        manifest_text: str,
        new_value: str,
    ) -> None:
        """A shape the rewriter does not recognize (A3: a hand-quoted YAML
        value; a non-pnpm packageManager; a configDependencies block that
        does not directly contain the pin) aborts with False and leaves the
        file byte-identical — no rewrite is better than a wrong rewrite.
        """
        path = tmp_path / "manifest"
        _ = path.write_text(manifest_text)

        assert rewriter(path, new_value) is False
        assert path.read_text() == manifest_text


class TestCombinedFlagAndOrdinaryRun:
    """A flagged --include-pnpm run whose scan ALSO carries an ordinary
    eligible update (L11 decision 3): the L8 strict rule binds the
    TOOLCHAIN STEP — the ordinary apply path's own install-typed cwd-root
    calls (the exact-pin resync install) may legitimately run pre-bump and
    must not abort the toolchain bump.
    """

    def test_pre_bump_ordinary_install_does_not_abort_the_toolchain_bump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ordinary path's pre-bump root install (its exact-pin resync,
        driven through the real code path: the fake `pnpm add` leaves a
        range specifier so _ensure_exact_specifier rewrites to the exact
        pin and issues its `pnpm install` at the repo root) does not abort
        the toolchain bump: all three manifests end bumped, the toolchain
        step's verification installs ran post-bump (pins snapshot = new,
        L7), and the run succeeds.
        """
        root, _run_calls, install_states = _toolchain_fakes(
            monkeypatch,
            tmp_path,
            scan_json=_combined_scan_json("analecta"),
            ordinary_registry={"time": {"8.74.0": _old_release().isoformat()}},
            add_leaves_range=True,
        )
        pr_body_file = tmp_path / "pr-body.md"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "deps_update.py",
                "--include-pnpm",
                "--pr-body-file",
                str(pr_body_file),
            ],
        )

        exit_code: int | str | None = None
        try:
            deps_update.main()
        except SystemExit as exc:
            exit_code = exc.code

        # (i) The composition is really exercised: an install-typed
        # cwd-root invocation ran while the manifests still carried the old
        # pin. The toolchain step runs LAST in main, so any pre-bump root
        # install is by construction the ordinary path's (the exact-pin
        # resync). If none exists, the fixture failed to trigger the
        # resync and the assertions below are vacuous.
        root_installs = [(cwd, pins) for cwd, pins in install_states if cwd == root]
        pre_bump = [
            pins for _cwd, pins in root_installs if _NEW_PIN not in str(pins[0])
        ]
        assert pre_bump != [], (
            "no install-typed cwd-root invocation ran with pre-bump pins —"
            " the ordinary path's exact-pin resync install never fired, so"
            " this test is not exercising the combined-run composition"
        )

        # (ii) The run succeeds despite that pre-bump ordinary install.
        assert exit_code is None

        # (iii) All three manifests end bumped — the pre-bump ordinary
        # install did not abort the toolchain bump.
        pkg_manager, config_dep, mise_pnpm = _toolchain_pins(root)
        assert _NEW_PIN in str(pkg_manager)
        assert config_dep == _NEW_PIN
        assert mise_pnpm == _NEW_PIN

        # (iv) The toolchain step's verification installs ran post-bump:
        # at least one install-typed root invocation saw all three pins
        # already at the new pin (L7 ordering observable; nothing runs
        # after the toolchain step, so these are its verification installs).
        post_bump = [
            pins
            for _cwd, pins in root_installs
            if _NEW_PIN in str(pins[0]) and pins[1] == _NEW_PIN and pins[2] == _NEW_PIN
        ]
        assert post_bump != []

        # (v) The ordinary update applied too: the exact-pin rewrite left
        # typescript-eslint at its exact new pin and the PR body reports it
        # as an applied row (the S3 boundary still holds — no pnpm row).
        pkg: dict[str, Any] = json.loads((root / "package.json").read_text())
        assert pkg["devDependencies"]["typescript-eslint"] == "8.74.0"
        body = pr_body_file.read_text()
        node_section = body.split("### Node / pnpm", 1)[1]
        assert "| `typescript-eslint` | `analecta` |" in node_section
        assert "| `pnpm` |" not in node_section
