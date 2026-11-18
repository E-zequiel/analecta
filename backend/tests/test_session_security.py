"""T5 (high-risk unit): session discovery, token auth, vault flock.

Owner decision D5 — security-first for the CLI era:
- The sidecar publishes a discovery file (port + session token, chmod 0600)
  and requires the token on every /api/v1 route (Bearer header; the SSE
  route additionally accepts ?token= because EventSource cannot set
  headers).
- The vault flock is held by any process that owns writes (sidecar
  included); a second writer fails instead of racing.
"""

import json
import os
import stat
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from analecta.session_security import (
    SESSION_FILE_NAME,
    VaultLock,
    session_auth_dependency,
)

# ---------------------------------------------------------------------------
# Discovery file
# ---------------------------------------------------------------------------


def test_discovery_write_creates_0600_file(tmp_path: Path) -> None:
    from analecta.session_security import SessionDiscovery

    d = SessionDiscovery(tmp_path)
    d.write(port=4242, token="tok-abc", pid=os.getpid())

    path = tmp_path / SESSION_FILE_NAME
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"discovery file must be 0600, got {oct(mode)}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["port"] == 4242
    assert data["token"] == "tok-abc"
    assert data["pid"] == os.getpid()


def test_discovery_remove_deletes_file(tmp_path: Path) -> None:
    from analecta.session_security import SessionDiscovery

    d = SessionDiscovery(tmp_path)
    d.write(port=1, token="t", pid=os.getpid())
    d.remove()
    assert not (tmp_path / SESSION_FILE_NAME).exists()


def test_discovery_remove_is_idempotent(tmp_path: Path) -> None:
    from analecta.session_security import SessionDiscovery

    d = SessionDiscovery(tmp_path)
    d.remove()  # no file yet
    assert not (tmp_path / SESSION_FILE_NAME).exists()


# ---------------------------------------------------------------------------
# Token auth dependency
# ---------------------------------------------------------------------------


def _app_with_token(token: str | None) -> FastAPI:
    from fastapi import Depends

    app = FastAPI()

    @app.get("/ping", dependencies=[Depends(session_auth_dependency)])
    def ping() -> dict[str, str]:
        return {"ok": "1"}

    app.state.session_token = token
    return app


def test_auth_rejects_missing_and_wrong_token() -> None:
    app = _app_with_token("secret")
    client = TestClient(app)
    assert client.get("/ping").status_code == 401
    assert (
        client.get("/ping", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    assert client.get("/ping", headers={"X-Session-Token": "wrong"}).status_code == 401


def test_auth_accepts_bearer_and_header_token() -> None:
    app = _app_with_token("secret")
    client = TestClient(app)
    assert (
        client.get("/ping", headers={"Authorization": "Bearer secret"}).status_code
        == 200
    )
    assert client.get("/ping", headers={"X-Session-Token": "secret"}).status_code == 200


def test_auth_open_when_no_token_configured() -> None:
    """Dev/test mode: sidecars started without a token are not enforced."""
    app = _app_with_token(None)
    client = TestClient(app)
    assert client.get("/ping").status_code == 200


# ---------------------------------------------------------------------------
# Vault flock
# ---------------------------------------------------------------------------


def test_vault_lock_acquire_and_release(tmp_path: Path) -> None:
    lock = VaultLock(tmp_path / "vault")
    fd = lock.acquire()
    assert fd is not None
    try:
        assert (tmp_path / "vault" / ".analecta.lock").exists()
    finally:
        lock.release(fd)
    # Re-acquirable after release.
    fd2 = lock.acquire()
    assert fd2 is not None
    lock.release(fd2)


def test_vault_lock_second_writer_fails(tmp_path: Path) -> None:
    lock = VaultLock(tmp_path / "vault")
    fd = lock.acquire()
    assert fd is not None
    try:
        second = VaultLock(tmp_path / "vault").acquire()
        assert second is None, "second writer must fail, not race"
    finally:
        lock.release(fd)


def test_vault_lock_creates_vault_dir_if_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    lock = VaultLock(vault)
    fd = lock.acquire()
    assert fd is not None
    lock.release(fd)
    assert vault.is_dir()
