"""Tests for scripts/verify-provenance.py's lockfile parser.

The script's filename contains a hyphen, so it cannot be imported by name; it is
loaded from its path instead. Only the parser is exercised here — the script's
network sweep (registry + Sigstore round trips per package) is out of scope.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify-provenance.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("verify_provenance", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_provenance"] = module
    spec.loader.exec_module(module)
    return module


vp = _load_module()


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
def test_parses_quoted_entries(tmp_path: Path, name: str, version: str) -> None:
    """Quoted entries parse for both scoped and unscoped names."""
    path = _write_lockfile(
        tmp_path,
        f"packages:\n\n  '{name}@{version}':\n"
        "    resolution: {integrity: sha512-AAAABBBBCCCC==}\n",
    )
    assert vp.parse_lockfile(path) == {(name, version): "sha512-AAAABBBBCCCC=="}


def test_parses_scoped_entry_from_env_document(tmp_path: Path) -> None:
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
    parsed = vp.parse_lockfile(path)
    assert parsed == {
        ("@pnpm/exe.darwin-arm64", "12.4.2"): "sha512-ENVENVENV==",
        ("@sveltejs/kit", "2.70.3"): "sha512-PROJPROJ==",
    }


def test_skips_entry_without_integrity(tmp_path: Path) -> None:
    path = _write_lockfile(
        tmp_path,
        "packages:\n\n  'no-integrity@1.0.0':\n    resolution: {}\n",
    )
    assert vp.parse_lockfile(path) == {}


def test_excludes_zkochan_entries(tmp_path: Path) -> None:
    path = _write_lockfile(
        tmp_path,
        "packages:\n\n  '@zkochan/internal@1.0.0':\n"
        "    resolution: {integrity: sha512-ZKOCHAN==}\n",
    )
    assert vp.parse_lockfile(path) == {}


def test_parses_every_scoped_entry_in_the_repo_lockfile() -> None:
    """Non-vacuous check against the real lockfile: scoped packages are seen.

    Before the fix this returned zero scoped packages while the lockfile carried
    hundreds of them — the script reported success for the unscoped subset only.
    """
    parsed = vp.parse_lockfile(vp.LOCKFILE)
    scoped = [entry for entry in parsed if entry[0].startswith("@")]
    assert len(scoped) > 100, f"expected many scoped packages, got {len(scoped)}"
    assert ("devalue", "5.9.2") in parsed
