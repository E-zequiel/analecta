import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from analecta.api.deps import get_config as _dep_get_config
from analecta.config import AppConfig, save_config

log = logging.getLogger(__name__)
router = APIRouter()


class ConfigOut(BaseModel):
    """Serialised application configuration returned by the API.

    Attributes:
        vault_path: Absolute path to the vault directory.
        font_variant: Reading-font selection (``regular`` or ``bricolage``).
        ui_font_size: Font size for UI chrome in pixels.
        reading_font_size: Font size for reading area in pixels.
        theme: UI colour theme (``dark`` or ``light``).
        accent_color: Active accent colour name.
    """

    vault_path: str
    font_variant: str
    ui_font_size: float
    reading_font_size: float
    theme: str
    accent_color: str
    open_tab_ids: list[str]
    active_tab_id: str
    first_run: bool
    close_to_tray: bool


class ConfigIn(BaseModel):
    """Partial-update body for PUT /config.

    Attributes:
        vault_path: New vault path string, if provided.
        font_variant: New reading-font variant, if provided.
        ui_font_size: New UI chrome font size in pixels, if provided.
        reading_font_size: New reading area font size in pixels, if provided.
        theme: New UI colour theme, if provided.
        accent_color: New accent colour name, if provided.
        open_tab_ids: Ordered list of open tab IDs for persistence, if provided.
        active_tab_id: Currently active tab ID for persistence, if provided.
    """

    vault_path: str | None = None
    font_variant: Literal["regular", "bricolage"] | None = None
    ui_font_size: float | None = None
    reading_font_size: float | None = None
    theme: Literal["dark", "light"] | None = None
    accent_color: Literal["red", "yellow", "green", "cyan"] | None = None
    open_tab_ids: list[str] | None = None
    active_tab_id: str | None = None
    close_to_tray: bool | None = None


_BLOCKED_VAULT_PREFIXES = {
    Path("/"),
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
    Path("/boot"),
}


def _validate_vault_path(raw: str) -> Path:
    p = Path(raw).expanduser().resolve()
    if p in _BLOCKED_VAULT_PREFIXES or any(
        p == blocked or str(p).startswith(str(blocked) + "/")
        for blocked in _BLOCKED_VAULT_PREFIXES - {Path("/")}
    ):
        raise HTTPException(
            status_code=422, detail="vault_path points to a system directory"
        )
    return p


def _config_out(cfg: AppConfig) -> ConfigOut:
    return ConfigOut(
        vault_path=str(cfg.vault_path),
        font_variant=cfg.font_variant,
        ui_font_size=cfg.ui_font_size,
        reading_font_size=cfg.reading_font_size,
        theme=cfg.theme,
        accent_color=cfg.accent_color,
        open_tab_ids=list(cfg.open_tab_ids),
        active_tab_id=cfg.active_tab_id,
        first_run=cfg.first_run,
        close_to_tray=cfg.close_to_tray,
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
        vault_path=_validate_vault_path(body.vault_path)
        if body.vault_path is not None
        else config.vault_path,
        font_variant=body.font_variant
        if body.font_variant is not None
        else config.font_variant,
        ui_font_size=body.ui_font_size
        if body.ui_font_size is not None
        else config.ui_font_size,
        reading_font_size=body.reading_font_size
        if body.reading_font_size is not None
        else config.reading_font_size,
        theme=body.theme if body.theme is not None else config.theme,
        accent_color=body.accent_color
        if body.accent_color is not None
        else config.accent_color,
        open_tab_ids=body.open_tab_ids
        if body.open_tab_ids is not None
        else config.open_tab_ids,
        active_tab_id=body.active_tab_id
        if body.active_tab_id is not None
        else config.active_tab_id,
        close_to_tray=body.close_to_tray
        if body.close_to_tray is not None
        else config.close_to_tray,
    )
    await asyncio.to_thread(save_config, updated)
    request.app.state.config = updated
    return _config_out(updated)
