import logging
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

CONFIG_PATH = Path.home() / ".config" / "analecta" / "config.toml"
_LOG_PATH = Path.home() / ".local" / "share" / "analecta" / "analecta.log"


class AppConfig(BaseModel):
    """Application configuration loaded from ``~/.config/analecta/config.toml``.

    Attributes:
        vault_path: Root directory of the local vault.
        font_variant: JetBrains Mono variant to load (``regular`` or ``nerd``).
        update_channel: Release channel for the built-in updater.
        virustotal_enabled: Whether to offer VirusTotal URL scanning. Requires
            the user's API key to be present in the system keyring. Disabled
            by default.
    """

    vault_path: Path = Path.home() / ".local" / "share" / "analecta" / "vault"
    font_variant: Literal["regular", "nerd"] = "regular"
    update_channel: Literal["stable", "dev"] = "stable"
    virustotal_enabled: bool = False

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
        return AppConfig()
    with config_path.open("rb") as fh:
        data = tomllib.load(fh)
    return AppConfig.model_validate(data)


def setup_logging(dev: bool = False) -> None:
    """Configure root logger with stream and file handlers.

    Args:
        dev: If True, sets level to DEBUG and adds a StreamHandler.
    """
    log_level = logging.DEBUG if dev else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(log_level)

    if dev:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(_LOG_PATH)
    fh.setFormatter(fmt)
    root.addHandler(fh)
