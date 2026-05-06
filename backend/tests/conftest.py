import os

import keyring
import pytest
from keyring.backend import KeyringBackend

# Run Qt tests headless when no display is available.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _MemoryKeyring(KeyringBackend):
    """In-memory keyring backend for tests — no system keyring calls."""

    priority = 0

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:  # type: ignore[override]
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture(autouse=True)
def _memory_keyring() -> None:
    """Replace the system keyring with an in-memory backend for every test."""
    keyring.set_keyring(_MemoryKeyring())
