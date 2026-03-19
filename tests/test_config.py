"""Tests for configuration loading."""

import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from slack_monitor.config import _apply_env_overrides, _flatten_toml, load_config
from slack_monitor.models import AppConfig


class TestFlattenToml:
    def test_flattens_llm_section(self):
        raw = {"llm": {"base_url": "http://x", "model": "m"}}
        flat = _flatten_toml(raw)
        assert flat["base_url"] == "http://x"
        assert flat["model"] == "m"

    def test_flattens_buffer_section(self):
        raw = {"buffer": {"window_seconds": 30, "max_messages": 20}}
        flat = _flatten_toml(raw)
        assert flat["window_seconds"] == 30

    def test_flattens_output_section(self):
        raw = {"output": {"show_raw": True}}
        flat = _flatten_toml(raw)
        assert flat["show_raw"] is True

    def test_api_key_in_llm_section_ignored(self):
        """API keys in config file must be ignored for security."""
        raw = {"llm": {"api_key": "secret", "model": "m"}}
        flat = _flatten_toml(raw)
        assert "api_key" not in flat

    def test_non_section_keys_preserved(self):
        raw = {"some_top_level": "value"}
        flat = _flatten_toml(raw)
        assert flat["some_top_level"] == "value"


class TestApplyEnvOverrides:
    def test_string_override(self):
        with patch.dict(os.environ, {"SLACK_MONITOR_BASE_URL": "http://override"}):
            flat = _apply_env_overrides({})
            assert flat["base_url"] == "http://override"

    def test_int_override(self):
        with patch.dict(os.environ, {"SLACK_MONITOR_WINDOW_SECONDS": "120"}):
            flat = _apply_env_overrides({})
            assert flat["window_seconds"] == 120

    def test_float_override(self):
        with patch.dict(os.environ, {"SLACK_MONITOR_TEMPERATURE": "0.7"}):
            flat = _apply_env_overrides({})
            assert flat["temperature"] == 0.7

    def test_bool_true_override(self):
        for truthy in ("1", "true", "yes", "True", "YES"):
            with patch.dict(os.environ, {"SLACK_MONITOR_SHOW_RAW": truthy}):
                flat = _apply_env_overrides({})
                assert flat["show_raw"] is True

    def test_bool_false_override(self):
        with patch.dict(os.environ, {"SLACK_MONITOR_SHOW_RAW": "false"}):
            flat = _apply_env_overrides({})
            assert flat["show_raw"] is False

    def test_invalid_int_ignored(self):
        with patch.dict(os.environ, {"SLACK_MONITOR_WINDOW_SECONDS": "not_a_number"}):
            flat = _apply_env_overrides({"window_seconds": 60})
            assert flat["window_seconds"] == 60  # original value preserved

    def test_no_env_vars_no_change(self):
        env_backup = {k: v for k, v in os.environ.items() if k.startswith("SLACK_MONITOR_")}
        for k in env_backup:
            del os.environ[k]
        try:
            flat = _apply_env_overrides({"model": "original"})
            assert flat["model"] == "original"
        finally:
            os.environ.update(env_backup)


class TestLoadConfig:
    def test_load_defaults_without_file(self):
        """load_config with no file should return defaults."""
        with patch.dict(
            os.environ,
            {"SLACK_MONITOR_API_KEY": "test-key"},
            clear=False,
        ):
            config = load_config(config_path=Path("/nonexistent/path.toml"))
        assert isinstance(config, AppConfig)
        assert config.api_key == "test-key"

    def test_api_key_from_slack_monitor_env(self):
        with patch.dict(os.environ, {"SLACK_MONITOR_API_KEY": "my-key"}, clear=False):
            config = load_config(config_path=Path("/nonexistent"))
        assert config.api_key == "my-key"

    def test_api_key_from_openai_env(self):
        env = {k: "" for k in os.environ if k in ("SLACK_MONITOR_API_KEY",)}
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "openai-key", **env},
            clear=False,
        ):
            # Remove SLACK_MONITOR_API_KEY if present
            os.environ.pop("SLACK_MONITOR_API_KEY", None)
            config = load_config(config_path=Path("/nonexistent"))
        assert config.api_key == "openai-key"

    def test_load_from_toml_file(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[llm]\nbase_url = "http://myserver:8080/v1"\nmodel = "my-model"\n'
            "[buffer]\nwindow_seconds = 30\n"
        )
        with patch.dict(os.environ, {"SLACK_MONITOR_API_KEY": "k"}, clear=False):
            config = load_config(config_path=config_file)
        assert config.base_url == "http://myserver:8080/v1"
        assert config.model == "my-model"
        assert config.window_seconds == 30

    def test_env_overrides_toml(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text('[llm]\nmodel = "file-model"\n')
        with patch.dict(
            os.environ,
            {"SLACK_MONITOR_MODEL": "env-model", "SLACK_MONITOR_API_KEY": "k"},
            clear=False,
        ):
            config = load_config(config_path=config_file)
        assert config.model == "env-model"
