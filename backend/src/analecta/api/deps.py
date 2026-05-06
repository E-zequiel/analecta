from typing import cast

from fastapi import Request

from analecta.api.events import EventBus
from analecta.config import AppConfig
from analecta.storage.index import VaultIndex
from analecta.storage.vault import VaultManager


def get_config(request: Request) -> AppConfig:
    """Return the singleton AppConfig from application state.

    Args:
        request: Current HTTP request (injected by FastAPI).

    Returns:
        The AppConfig instance loaded at startup.
    """
    return cast(AppConfig, request.app.state.config)


def get_index(request: Request) -> VaultIndex:
    """Return the singleton VaultIndex from application state.

    Args:
        request: Current HTTP request (injected by FastAPI).

    Returns:
        The shared VaultIndex instance opened at startup.
    """
    return cast(VaultIndex, request.app.state.index)


def get_vault(request: Request) -> VaultManager:
    """Return a VaultManager for the configured vault path.

    Args:
        request: Current HTTP request (injected by FastAPI).

    Returns:
        A new VaultManager instance (lightweight, no persistent state).
    """
    config = get_config(request)
    return VaultManager(config.vault_path)


def get_event_bus(request: Request) -> EventBus:
    """Return the singleton EventBus from application state.

    Args:
        request: Current HTTP request (injected by FastAPI).

    Returns:
        The shared EventBus used to publish SSE events.
    """
    return cast(EventBus, request.app.state.event_bus)
