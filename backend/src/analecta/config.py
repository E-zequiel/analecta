import logging
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, field_validator

CONFIG_PATH = Path.home() / ".config" / "analecta" / "config.toml"
_LOG_PATH = Path.home() / ".local" / "share" / "analecta" / "analecta-sidecar.log"


class AppConfig(BaseModel):
    """Application configuration loaded from ``~/.config/analecta/config.toml``.

    Attributes:
        vault_path: Root directory of the local vault.
        font_variant: Reading-font selection — ``regular`` (JetBrains Mono) or
            ``bricolage`` (Bricolage Grotesque). The UI font is always Bricolage
            Grotesque and is not user-selectable.
        ui_font_size: Font size for UI chrome (sidebar, toolbar) in pixels.
        reading_font_size: Font size for article reading area in pixels.
        theme: UI colour theme — ``dark`` or ``light``.
        accent_color: Active accent colour drawn from the Tokyo Night palette.
    """

    vault_path: Path = Path.home() / "Documents" / "Analecta"
    first_run: bool = False
    font_variant: Literal["regular", "bricolage"] = "regular"
    ui_font_size: float = 17.0
    reading_font_size: float = 18.0
    theme: Literal["dark", "light"] = "dark"
    accent_color: Literal["red", "yellow", "green", "magenta"] = "yellow"
    open_tab_ids: list[str] = ["section-library"]
    active_tab_id: str = "section-library"
    close_to_tray: bool = False

    @field_validator("vault_path", mode="before")
    @classmethod
    def _expand(cls, v: object) -> object:
        return Path(str(v)).expanduser() if v is not None else v


def load_config(config_path: Path = CONFIG_PATH) -> AppConfig:
    """Load configuration from TOML file, falling back to defaults.

    Args:
        config_path: Path to config.toml. Defaults to ~/.config/analecta/config.toml.

    Returns:
        Validated AppConfig instance.
    """
    if not config_path.exists():
        return AppConfig(first_run=True)
    with config_path.open("rb") as fh:
        data = tomllib.load(fh)
    return AppConfig.model_validate(data)


def save_config(config: AppConfig, config_path: Path = CONFIG_PATH) -> None:
    """Persist *config* to TOML at *config_path*.

    Creates parent directories if they do not exist.

    Args:
        config: Configuration to write.
        config_path: Destination path. Defaults to the standard config file.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "vault_path": str(config.vault_path),
        "font_variant": config.font_variant,
        "ui_font_size": config.ui_font_size,
        "reading_font_size": config.reading_font_size,
        "theme": config.theme,
        "accent_color": config.accent_color,
        "open_tab_ids": list(config.open_tab_ids),
        "active_tab_id": config.active_tab_id,
        "close_to_tray": config.close_to_tray,
    }
    with config_path.open("wb") as fh:
        tomli_w.dump(data, fh)


def setup_logging(dev: bool = False) -> None:
    """Configure root logger with stream and file handlers.

    Args:
        dev: If True, sets level to DEBUG and adds a StreamHandler.
    """
    log_level = logging.DEBUG if dev else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(log_level)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(_LOG_PATH)
    fh.setFormatter(fmt)
    root.addHandler(fh)
