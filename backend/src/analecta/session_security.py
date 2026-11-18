"""Session security for the CLI era (owner decision D5).

Security-first primitives shared by the sidecar and the future CLI:

- **Discovery file**: the sidecar publishes its loopback port and a per-run
  session token to ``<data_dir>/session.json`` with mode 0600. The Electron
  shell reads the token from stdout (``SIDECAR_TOKEN:``); CLI bots read the
  file. Removed on sidecar shutdown, best-effort.
- **Token auth**: every ``/api/v1`` route requires the token (``Authorization:
  Bearer <token>`` or ``X-Session-Token``). The SSE route additionally accepts
  ``?token=`` because ``EventSource`` cannot set headers. A sidecar started
  without a token (explicit ``ANALECTA_INSECURE_DEV=1`` dev runs) is not
  enforced — production always sets one.
- **Vault flock**: writing processes hold an exclusive non-blocking ``flock``
  on ``<vault>/.analecta.lock`` for their whole lifetime. A second writer
  fails instead of racing: the lock is a filesystem property, not a
  convention (spec §3/D5).
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

SESSION_FILE_NAME = "session.json"
LOCK_FILE_NAME = ".analecta.lock"


def generate_session_token() -> str:
    """Return a high-entropy per-session token (D5)."""
    return secrets.token_urlsafe(32)


class SessionDiscovery:
    """Publish/read the sidecar's port + token discovery file."""

    def __init__(self, data_dir: Path) -> None:
        """Create a handle for the discovery file inside *data_dir*.

        Args:
            data_dir: Directory holding ``session.json`` (typically
                ``~/.local/share/analecta``).
        """
        self._dir = data_dir

    @property
    def path(self) -> Path:
        """Absolute path of the discovery file."""
        return self._dir / SESSION_FILE_NAME

    def write(self, *, port: int, token: str, pid: int) -> None:
        """Write the discovery file with owner-only permissions.

        Args:
            port: The sidecar's loopback port.
            token: The per-session token.
            pid: The sidecar process id (staleness check for readers).
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"port": port, "token": token, "pid": pid}, ensure_ascii=False
        )
        path = self.path
        # 0600 from creation: no window where the token is world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def read(self) -> dict[str, Any] | None:
        """Return the published payload, or ``None`` if absent/unreadable.

        Returns:
            ``{"port", "token", "pid"}`` when a file exists.
        """
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return None

    def remove(self) -> None:
        """Best-effort removal (idempotent)."""
        try:
            self.path.unlink()
        except OSError:
            pass


def session_auth_dependency(request: Request) -> None:
    """Require the session token when the sidecar runs with one.

    Reads ``app.state.session_token``; ``None`` means the sidecar was started
    without token enforcement (explicit dev opt-out) and the request passes.

    Args:
        request: The incoming request.

    Raises:
        HTTPException: 401 when the token is required but missing or wrong.
    """
    expected = getattr(request.app.state, "session_token", None)
    if expected is None:
        return
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer ") and header[len("Bearer ") :] == expected:
        return
    if request.headers.get("X-Session-Token") == expected:
        return
    query = request.query_params.get("token")
    if query == expected:
        # EventSource cannot set headers; SSE carries the token in the query.
        return
    raise HTTPException(status_code=401, detail="Invalid or missing session token")


class VaultLock:
    """Exclusive, non-blocking vault lock for writing processes (D5)."""

    def __init__(self, vault_path: Path) -> None:
        """Bind the lock to *vault_path*.

        Args:
            vault_path: Root directory of the local vault; the lock file is
                ``<vault>/.analecta.lock``.
        """
        self._vault_path = vault_path

    @property
    def path(self) -> Path:
        """Absolute path of the lock file."""
        return self._vault_path / LOCK_FILE_NAME

    def acquire(self) -> int | None:
        """Try to take the exclusive lock without blocking.

        Returns:
            The open lock-file descriptor while held, or ``None`` when
            another process already owns the vault.
        """
        self._vault_path.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return fd

    def release(self, fd: int | None) -> None:
        """Release the lock previously acquired.

        Args:
            fd: The descriptor returned by :meth:`acquire` (``None`` is a
                no-op).
        """
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
