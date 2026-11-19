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
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "analecta"
PACKAGED_BINARY = REPO_ROOT / "binaries" / "analecta-sidecar" / "analecta-sidecar"


def _run_wrapper(*args: str, env_extra: dict[str, str] | None = None):
    """Run the wrapper with ``bash -x`` and capture both streams.

    The xtrace on stderr records every command before execution, including
    the final ``exec`` — which is how the routed target is observed.
    """
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        ["bash", "-x", str(WRAPPER), *args],
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
    assert str(PACKAGED_BINARY) in proc.stderr


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_dev_flag_routes_to_python_fallback(value: str) -> None:
    """Any truthy ``ANALECTA_CLI_DEV`` spelling routes to the dev fallback."""
    proc = _run_wrapper("--help", env_extra={"ANALECTA_CLI_DEV": value})
    assert proc.returncode == 0
    assert "subcommands of the sidecar binary" in proc.stdout
    assert "python -m analecta" in proc.stderr
