import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from analecta.api.deps import get_config as _dep_get_config
from analecta.config import AppConfig, save_config

log = logging.getLogger(__name__)
router = APIRouter()


class ConfigOut(BaseModel):
    """Serialised application configuration returned by the API.

    Attributes:
        vault_path: Absolute path to the vault directory.
        font_variant: Font selection (``regular``, ``nerd``, or ``custom``).
        custom_font_path: Path to user-supplied ``.ttf`` when variant is ``custom``.
        update_channel: Release channel (``stable`` or ``dev``).
        virustotal_enabled: Whether VirusTotal scanning is enabled.
    """

    vault_path: str
    font_variant: str
    custom_font_path: str | None
    update_channel: str
    virustotal_enabled: bool


class ConfigIn(BaseModel):
    """Partial-update body for PUT /config.

    Attributes:
        vault_path: New vault path string, if provided.
        font_variant: New font variant, if provided.
        custom_font_path: New custom font path; ``None`` clears it.
        update_channel: New update channel, if provided.
        virustotal_enabled: New VT toggle value, if provided.
    """

    vault_path: str | None = None
    font_variant: Literal["regular", "nerd", "custom"] | None = None
    custom_font_path: str | None = None
    update_channel: Literal["stable", "dev"] | None = None
    virustotal_enabled: bool | None = None


def _config_out(cfg: AppConfig) -> ConfigOut:
    return ConfigOut(
        vault_path=str(cfg.vault_path),
        font_variant=cfg.font_variant,
        custom_font_path=cfg.custom_font_path,
        update_channel=cfg.update_channel,
        virustotal_enabled=cfg.virustotal_enabled,
    )


@router.get("/config", response_model=ConfigOut)
async def read_config(config: AppConfig = Depends(_dep_get_config)) -> ConfigOut:
    """Return the current application configuration.

    Args:
        config: Injected AppConfig singleton.

    Returns:
        Serialised configuration with vault_path as a string.
    """
    return _config_out(config)


@router.put("/config", response_model=ConfigOut)
async def update_config(
    body: ConfigIn,
    request: Request,
    config: AppConfig = Depends(_dep_get_config),
) -> ConfigOut:
    """Persist a partial or full configuration update.

    Merges *body* onto the current config, writes to disk, and updates the
    in-memory singleton so subsequent requests see the new values immediately.

    Args:
        body: Fields to update (all optional).
        request: Current HTTP request (used to update app state).
        config: Injected AppConfig singleton.

    Returns:
        The updated configuration.
    """
    updated = AppConfig(
        vault_path=Path(body.vault_path)
        if body.vault_path is not None
        else config.vault_path,
        font_variant=body.font_variant
        if body.font_variant is not None
        else config.font_variant,
        custom_font_path=body.custom_font_path
        if "custom_font_path" in body.model_fields_set
        else config.custom_font_path,
        update_channel=body.update_channel
        if body.update_channel is not None
        else config.update_channel,
        virustotal_enabled=body.virustotal_enabled
        if body.virustotal_enabled is not None
        else config.virustotal_enabled,
    )
    await asyncio.to_thread(save_config, updated)
    request.app.state.config = updated
    return _config_out(updated)
