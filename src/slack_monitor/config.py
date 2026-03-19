"""Configuration loading with TOML file + environment variable overrides.

Security note: API keys are NEVER stored in TOML files.
They must be provided via SLACK_MONITOR_API_KEY or OPENAI_API_KEY environment variables.
"""

import logging
import os
import tomllib
from pathlib import Path
from typing import Any

from slack_monitor.models import AppConfig

_log = logging.getLogger(__name__)

# Default config file paths (searched in order)
_DEFAULT_CONFIG_PATHS = [
    Path("./config.toml"),
    Path("~/.config/slack-monitor/config.toml").expanduser(),
]

# Environment variable → config field mapping
_ENV_MAP: dict[str, tuple[str, type]] = {
    "SLACK_MONITOR_BASE_URL": ("base_url", str),
    "SLACK_MONITOR_MODEL": ("model", str),
    "SLACK_MONITOR_MAX_TOKENS": ("max_tokens", int),
    "SLACK_MONITOR_TEMPERATURE": ("temperature", float),
    "SLACK_MONITOR_WINDOW_SECONDS": ("window_seconds", int),
    "SLACK_MONITOR_TRIGGER_MESSAGES": ("trigger_messages", int),
    "SLACK_MONITOR_MAX_MESSAGES": ("max_messages", int),
    "SLACK_MONITOR_MAX_CHARS": ("max_chars", int),
    "SLACK_MONITOR_LANGUAGE": ("analysis_language", str),
    "SLACK_MONITOR_SHOW_RAW": ("show_raw", bool),
    "SLACK_MONITOR_TIMEZONE": ("timezone", str),
}


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load configuration from TOML file and apply environment variable overrides.

    API key is sourced exclusively from environment variables, never from TOML.

    Args:
        config_path: Explicit path to config.toml. If None, searches default locations.

    Returns:
        Validated AppConfig instance.

    Raises:
        ValueError: If no API key is found in environment variables.
    """
    raw: dict[str, Any] = {}

    path = _resolve_config_path(config_path)
    if path is not None:
        raw = _load_toml(path)
        _log.info("Loaded config from %s", path)
    else:
        _log.info("No config file found, using defaults")

    # Flatten nested TOML sections into flat dict
    flat = _flatten_toml(raw)

    # Apply environment variable overrides
    flat = _apply_env_overrides(flat)

    # API key: env var only (security requirement)
    api_key = os.environ.get("SLACK_MONITOR_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        flat["api_key"] = api_key
    elif "api_key" not in flat:
        # Allow running without explicit key for local LLMs (LM Studio default)
        flat["api_key"] = "lm-studio"
        _log.warning(
            "No API key found in SLACK_MONITOR_API_KEY or OPENAI_API_KEY. "
            "Using 'lm-studio' default (suitable for local LM Studio only)."
        )

    return AppConfig.model_validate(flat)


def _resolve_config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        if explicit.exists():
            return explicit
        _log.warning("Config file not found: %s", explicit)
        return None

    # Check SLACK_MONITOR_CONFIG env var
    env_path = os.environ.get("SLACK_MONITOR_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        _log.warning("Config file from SLACK_MONITOR_CONFIG not found: %s", env_path)

    for p in _DEFAULT_CONFIG_PATHS:
        if p.exists():
            return p

    return None


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except OSError as e:
        _log.error("Failed to read config file %s: %s", path, e)
        return {}
    except tomllib.TOMLDecodeError as e:
        _log.error("Failed to parse config file %s: %s", path, e)
        return {}


def _flatten_toml(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested TOML sections [llm], [buffer], [output] into a flat dict."""
    flat: dict[str, Any] = {}
    section_keys = {"llm", "buffer", "output"}
    for key, value in raw.items():
        if key in section_keys and isinstance(value, dict):
            # Security check: reject api_key in config file
            if "api_key" in value:
                _log.warning(
                    "api_key found in config file [%s] section — IGNORED for security. "
                    "Use SLACK_MONITOR_API_KEY or OPENAI_API_KEY environment variable.",
                    key,
                )
                value = {k: v for k, v in value.items() if k != "api_key"}
            flat.update(value)
        else:
            flat[key] = value
    return flat


def _apply_env_overrides(flat: dict[str, Any]) -> dict[str, Any]:
    for env_var, (field_name, field_type) in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is not None:
            try:
                if field_type is bool:
                    flat[field_name] = value.lower() in ("1", "true", "yes")
                else:
                    flat[field_name] = field_type(value)
                _log.debug("Env override: %s → %s=%r", env_var, field_name, flat[field_name])
            except (ValueError, TypeError) as e:
                _log.warning("Invalid value for %s=%r: %s (ignored)", env_var, value, e)
    return flat
