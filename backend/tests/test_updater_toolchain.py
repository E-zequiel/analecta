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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
