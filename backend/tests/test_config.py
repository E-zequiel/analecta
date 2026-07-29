from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from analecta.config import AppConfig, load_config

VALID_FONT_VARIANTS = {"regular", "bricolage"}


def test_appconfig_defaults():
    config = AppConfig()
    assert config.vault_path == Path.home() / "Documents" / "Analecta"
    assert config.font_variant == "regular"


def test_appconfig_vault_path_expanduser():
    config = AppConfig(vault_path="~/my-vault")  # pyright: ignore[reportArgumentType]
    assert not str(config.vault_path).startswith("~")


@given(st.text().filter(lambda v: v not in VALID_FONT_VARIANTS))
@settings(max_examples=50)
def test_appconfig_rejects_invalid_font_variant(value: str):
    with pytest.raises(ValidationError):
        AppConfig(font_variant=value)  # pyright: ignore[reportArgumentType]


def test_load_config_missing_file(tmp_path: Path):
    config = load_config(tmp_path / "nonexistent.toml")
    assert isinstance(config, AppConfig)
    assert config.font_variant == "regular"


def test_load_config_reads_vault_path(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('vault_path = "/tmp/test-vault"\n')
    config = load_config(cfg_file)
    assert config.vault_path == Path("/tmp/test-vault")


def test_load_config_invalid_field_raises(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('font_variant = "comic_sans"\n')
    with pytest.raises(ValidationError):
        load_config(cfg_file)


def test_appconfig_close_to_tray_default():
    assert AppConfig().close_to_tray is False


def test_load_config_reads_close_to_tray(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("close_to_tray = false\n")
    config = load_config(cfg_file)
    assert config.close_to_tray is False


def test_appconfig_accent_color_magenta_valid():
    assert AppConfig(accent_color="magenta").accent_color == "magenta"


def test_appconfig_rejects_removed_cyan_accent():
    with pytest.raises(ValidationError):
        AppConfig(accent_color="cyan")  # pyright: ignore[reportArgumentType]
