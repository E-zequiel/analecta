"""Contract tests for the ``scripts/analecta`` CLI wrapper.

The wrapper's externally observable behavior is pinned through its public
interface — running the script — with ``bash -x`` traces used to observe the
routed dispatch target (packaged binary vs dev fallback) without mocking
anything. Three behaviors are pinned:

- a bare invocation is rejected with exit code 64 and a usage hint on stderr;
- the default route dispatches the packaged sidecar binary;
- the ``ANALECTA_CLI_DEV`` opt-in (``1``, ``true`` or ``yes``) routes to the
  ``python -m analecta`` dev fallback.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "analecta"
PACKAGED_BINARY = REPO_ROOT / "binaries" / "analecta-sidecar" / "analecta-sidecar"
BASH = shutil.which("bash") or "/bin/bash"


def _run_wrapper(
    *args: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the wrapper with ``bash -x`` and capture both streams.

    The xtrace on stderr records every command before execution, including
    the final ``exec`` — which is how the routed target is observed. The
    ambient ``ANALECTA_CLI_DEV`` is stripped so tests stay deterministic
    regardless of the outer environment; *env_extra* wins when it sets it.
    """
    env = os.environ.copy()
    env.pop("ANALECTA_CLI_DEV", None)
    env.update(env_extra or {})
    return subprocess.run(
        # Absolute bash path: a stripped PATH (fake-PATH tests) must not
        # break resolving the interpreter itself, only the wrapper's tools.
        [BASH, "-x", str(WRAPPER), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_bare_invocation_is_rejected() -> None:
    """No subcommand: exit 64 and the usage hint on stderr, never a server."""
    proc = _run_wrapper()
    assert proc.returncode == 64
    assert "subcommand is required" in proc.stderr
    assert "usage: analecta" in proc.stderr


@pytest.mark.skipif(
    not PACKAGED_BINARY.exists(), reason="packaged sidecar binary not built"
)
def test_default_route_dispatches_packaged_binary() -> None:
    """Without the dev flag the wrapper execs the packaged sidecar binary."""
    proc = _run_wrapper("--help")
    assert proc.returncode == 0
    assert "subcommands of the sidecar binary" in proc.stdout
    # The exec line, not merely the binary path: the xtrace also echoes the
    # path inside the fallback-branch check, so only the actual dispatch
    # line distinguishes routes.
    assert f"+ exec {PACKAGED_BINARY}" in proc.stderr


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_dev_flag_routes_to_python_fallback(value: str) -> None:
    """Any truthy ``ANALECTA_CLI_DEV`` spelling routes to the dev fallback."""
    proc = _run_wrapper("--help", env_extra={"ANALECTA_CLI_DEV": value})
    assert proc.returncode == 0
    assert "subcommands of the sidecar binary" in proc.stdout
    assert "python -m analecta" in proc.stderr


def _stripped_path(base: Path, *tools: str) -> str:
    """PATH exposing only the wrapper's own shell needs plus *tools*.

    Simulates machines without mise (and optionally without uv): the wrapper
    needs ``readlink`` and ``dirname`` on PATH; everything else is a builtin.
    Each requested tool is symlinked from wherever it lives on this machine.
    *base* is the caller's ``tmp_path`` — pytest removes it after the test.
    """
    tool_dir = base / "fake-path"
    tool_dir.mkdir()
    for name in ("readlink", "dirname", *tools):
        source = shutil.which(name)
        if source is None:
            pytest.skip(f"{name} not available to build the fake PATH")
        os.symlink(source, tool_dir / name)
    return str(tool_dir)


@pytest.mark.skipif(
    shutil.which("uv") is None, reason="standalone uv not available on this machine"
)
def test_without_mise_routes_to_uv(tmp_path: Path) -> None:
    """No mise but standalone uv on PATH: the dev fallback runs ``uv run``."""
    proc = _run_wrapper(
        "--help",
        env_extra={
            "PATH": _stripped_path(tmp_path, "uv"),
            "ANALECTA_CLI_DEV": "1",
            # Routing-only test: keep uv from syncing the project environment
            # (network, package stores) — the existing venv is enough.
            "UV_NO_SYNC": "1",
        },
    )
    assert proc.returncode == 0
    assert "subcommands of the sidecar binary" in proc.stdout
    assert "+ exec uv run python -m analecta" in proc.stderr


@pytest.mark.skipif(
    not (REPO_ROOT / "backend" / ".venv" / "bin" / "python").exists(),
    reason="backend/.venv not built on this machine",
)
def test_without_mise_or_uv_routes_to_existing_venv(tmp_path: Path) -> None:
    """No mise, no uv, but backend/.venv exists: run its python directly."""
    proc = _run_wrapper(
        "--help",
        env_extra={"PATH": _stripped_path(tmp_path), "ANALECTA_CLI_DEV": "1"},
    )
    assert proc.returncode == 0
    assert "subcommands of the sidecar binary" in proc.stdout
    assert ".venv/bin/python -m analecta" in proc.stderr
