from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from analecta.config import AppConfig, load_config

VALID_FONT_VARIANTS = {"regular", "nerd"}
VALID_UPDATE_CHANNELS = {"stable", "dev"}


def test_appconfig_defaults():
    config = AppConfig()
    assert config.vault_path == Path.home() / ".local" / "share" / "analecta" / "vault"
    assert config.font_variant == "regular"
    assert config.update_channel == "stable"
    assert config.virustotal_enabled is False


def test_appconfig_vault_path_expanduser():
    config = AppConfig(vault_path="~/my-vault")
    assert not str(config.vault_path).startswith("~")


@given(st.text().filter(lambda v: v not in VALID_FONT_VARIANTS))
@settings(max_examples=50)
def test_appconfig_rejects_invalid_font_variant(value: str):
    with pytest.raises(ValidationError):
        AppConfig(font_variant=value)


@given(st.text().filter(lambda v: v not in VALID_UPDATE_CHANNELS))
@settings(max_examples=50)
def test_appconfig_rejects_invalid_update_channel(value: str):
    with pytest.raises(ValidationError):
        AppConfig(update_channel=value)


def test_load_config_missing_file(tmp_path: Path):
    config = load_config(tmp_path / "nonexistent.toml")
    assert isinstance(config, AppConfig)
    assert config.font_variant == "regular"


def test_load_config_reads_vault_path(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('vault_path = "/tmp/test-vault"\n')
    config = load_config(cfg_file)
    assert config.vault_path == Path("/tmp/test-vault")


def test_load_config_reads_update_channel(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('update_channel = "dev"\n')
    config = load_config(cfg_file)
    assert config.update_channel == "dev"


def test_load_config_reads_virustotal_enabled(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("virustotal_enabled = true\n")
    config = load_config(cfg_file)
    assert config.virustotal_enabled is True


def test_appconfig_virustotal_disabled_by_default():
    assert AppConfig().virustotal_enabled is False


def test_load_config_invalid_field_raises(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('font_variant = "comic_sans"\n')
    with pytest.raises(ValidationError):
        load_config(cfg_file)
