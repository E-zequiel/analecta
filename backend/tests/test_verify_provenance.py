"""Tests for scripts/verify-provenance.py's lockfile parser.

The script's filename contains a hyphen, so it cannot be imported by name; the
`vp` fixture loads it from its path. Only the parser is exercised here — the
script's network sweep (registry + Sigstore round trips per package) is out of
scope.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify-provenance.py"


@pytest.fixture(scope="module")
def vp() -> Any:
    """Load scripts/verify-provenance.py without touching global import state."""
    spec = importlib.util.spec_from_file_location("verify_provenance", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_lockfile(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pnpm-lock.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("@sveltejs/kit", "2.70.3"),
        ("@codemirror/state", "6.7.0"),
        ("devalue", "5.9.2"),
    ],
)
def test_parses_quoted_entries(
    vp: Any, tmp_path: Path, name: str, version: str
) -> None:
    """Quoted entries parse for both scoped and unscoped names."""
    path = _write_lockfile(
        tmp_path,
        f"packages:\n\n  '{name}@{version}':\n"
        "    resolution: {integrity: sha512-AAAABBBBCCCC==}\n",
    )
    assert vp.parse_lockfile(path) == {(name, version): "sha512-AAAABBBBCCCC=="}


def test_parses_scoped_entry_from_env_document(vp: Any, tmp_path: Path) -> None:
    """An entry in the leading env document is seen (two-document lockfile)."""
    path = _write_lockfile(
        tmp_path,
        "---\n"
        "lockfileVersion: '9.0'\n\n"
        "packages:\n\n"
        "  '@pnpm/exe.darwin-arm64@12.4.2':\n"
        "    resolution: {integrity: sha512-ENVENVENV==}\n\n"
        "---\n"
        "lockfileVersion: '9.0'\n\n"
        "packages:\n\n"
        "  '@sveltejs/kit@2.70.3':\n"
        "    resolution: {integrity: sha512-PROJPROJ==}\n",
    )
    assert vp.parse_lockfile(path) == {
        ("@pnpm/exe.darwin-arm64", "12.4.2"): "sha512-ENVENVENV==",
        ("@sveltejs/kit", "2.70.3"): "sha512-PROJPROJ==",
    }


def test_skips_entry_without_integrity(vp: Any, tmp_path: Path) -> None:
    path = _write_lockfile(
        tmp_path,
        "packages:\n\n  'no-integrity@1.0.0':\n    resolution: {}\n",
    )
    assert vp.parse_lockfile(path) == {}


def test_excludes_zkochan_entries(vp: Any, tmp_path: Path) -> None:
    path = _write_lockfile(
        tmp_path,
        "packages:\n\n  '@zkochan/internal@1.0.0':\n"
        "    resolution: {integrity: sha512-ZKOCHAN==}\n",
    )
    assert vp.parse_lockfile(path) == {}


def test_peer_suffixed_key_with_integrity_fails_loudly(vp: Any, tmp_path: Path) -> None:
    """A peer-suffixed key carrying a resolution is a parser gap, not a skip.

    pnpm writes peer suffixes on `snapshots:` keys (`'@keyv/bigmap@1.3.1(keyv@5.6.0)'`),
    which carry no integrity and are legitimately unparsed. If such a key ever
    carries an integrity line, the parser must report it instead of silently
    dropping it or swallowing the suffix into the name/version.
    """
    body = (
        "packages:\n\n"
        "  '@keyv/bigmap@1.3.1(keyv@5.6.0)':\n"
        "    resolution: {integrity: sha512-AAAA==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == ["@keyv/bigmap@1.3.1(keyv@5.6.0)"]
    with pytest.raises(RuntimeError, match="did not match the lockfile parser"):
        vp.parse_lockfile(path)


def test_peer_suffixed_key_without_integrity_is_skipped(
    vp: Any, tmp_path: Path
) -> None:
    """Snapshots-style keys are unparsed without raising — no false alarm."""
    body = (
        "snapshots:\n\n"
        "  '@keyv/bigmap@1.3.1(keyv@5.6.0)':\n"
        "    dependencies:\n"
        "      keyv: 5.6.0\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.parse_lockfile(path) == {}


def test_parenthesised_key_with_integrity_fails_loudly(vp: Any, tmp_path: Path) -> None:
    """A name containing a parenthesis must not be parsed as a truncated name."""
    body = (
        "packages:\n\n"
        "  'weird(thing)@1.0.0':\n"
        "    resolution: {integrity: sha512-BBBB==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == ["weird(thing)@1.0.0"]
    with pytest.raises(RuntimeError, match="did not match the lockfile parser"):
        vp.parse_lockfile(path)


def test_repo_lockfile_has_no_unparsed_package_entries(vp: Any) -> None:
    """Structural invariant on the real lockfile: the parser covers it.

    This is the guard the review asked for: if pnpm's lockfile shape drifts and
    the pattern stops matching package entries that carry a resolution, this
    fails here rather than silently under-reporting in CI.
    """
    assert vp.find_unmatched_package_keys(vp.LOCKFILE.read_text()) == []


def test_repo_lockfile_yields_scoped_entries(vp: Any) -> None:
    """Regression guard: scoped packages are parsed, not skipped.

    Before the fix the parser returned zero scoped entries while the lockfile
    carried hundreds — the script reported success over the unscoped subset only.
    """
    parsed = vp.parse_lockfile(vp.LOCKFILE)
    assert any(name.startswith("@") for name, _ in parsed)
    assert ("devalue", "5.9.2") in parsed
