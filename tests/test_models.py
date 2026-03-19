"""Tests for data models."""

import pytest
from pydantic import ValidationError

from slack_monitor.models import (
    ActivityLevel,
    AnalysisResult,
    AppConfig,
    PostType,
    SlackMessage,
)


class TestSlackMessage:
    def test_valid_stail_schema(self):
        msg = SlackMessage.model_validate(
            {
                "user_id": "U001",
                "user_name": "alice",
                "post_type": "user",
                "timestamp": "2026-03-19T10:44:00Z",
                "timestamp_unix": "1742377440.000100",
                "text": "Hello world",
                "is_reply": False,
            }
        )
        assert msg.user_name == "alice"
        assert msg.post_type == PostType.USER
        assert msg.text == "Hello world"

    def test_brief_schema_user_alias(self):
        """The 'user' field from the brief schema is accepted as user_name alias."""
        msg = SlackMessage.model_validate(
            {
                "user": "alice",
                "timestamp": "2026-03-19T10:44:00Z",
                "text": "Hello from brief schema",
            }
        )
        # 'user' field is handled in reader.py, not models.py
        # Here we test that extra fields are ignored
        assert msg.timestamp == "2026-03-19T10:44:00Z"

    def test_missing_timestamp_raises(self):
        with pytest.raises(ValidationError):
            SlackMessage.model_validate({"user_name": "alice", "text": "no timestamp"})

    def test_post_type_coercion_bot(self):
        msg = SlackMessage(timestamp="2026-01-01T00:00:00Z", post_type="bot")  # type: ignore[arg-type]
        assert msg.post_type == PostType.BOT

    def test_post_type_coercion_unknown(self):
        msg = SlackMessage(timestamp="2026-01-01T00:00:00Z", post_type="webhook")  # type: ignore[arg-type]
        assert msg.post_type == PostType.UNKNOWN

    def test_token_estimate(self):
        msg = SlackMessage(
            timestamp="2026-01-01T00:00:00Z",
            user_name="alice",
            text="Hello world",  # 11 chars + "alice" 5 chars = 16 // 3 = 5
        )
        assert msg.token_estimate() == 5

    def test_char_count(self):
        msg = SlackMessage(
            timestamp="2026-01-01T00:00:00Z",
            user_name="ab",
            text="cd",
        )
        assert msg.char_count() == 4

    def test_extra_fields_ignored(self):
        """Extra fields from stail (e.g., 'files', 'channel') are silently ignored."""
        msg = SlackMessage.model_validate(
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "text": "test",
                "files": [],
                "channel": "general",
            }
        )
        assert msg.text == "test"

    def test_none_user_name_defaults_to_empty(self):
        msg = SlackMessage(timestamp="2026-01-01T00:00:00Z", user_name=None)  # type: ignore[arg-type]
        assert msg.user_name == ""


class TestAnalysisResult:
    def test_defaults(self):
        result = AnalysisResult(
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-01-01T00:01:00Z",
            message_count=5,
        )
        assert result.topics == []
        assert result.sentiment == "neutral"
        assert result.activity_level == ActivityLevel.NORMAL
        assert result.key_events == []
        assert result.summary == ""

    def test_activity_level_coercion(self):
        result = AnalysisResult(
            window_start="",
            window_end="",
            message_count=0,
            activity_level="burst",
        )
        assert result.activity_level == ActivityLevel.BURST

    def test_extra_fields_ignored(self):
        result = AnalysisResult.model_validate(
            {
                "window_start": "",
                "window_end": "",
                "message_count": 0,
                "unknown_field": "should be ignored",
            }
        )
        assert not hasattr(result, "unknown_field")


class TestAppConfig:
    def test_defaults(self):
        config = AppConfig()
        assert config.base_url == "http://localhost:1234/v1"
        assert config.model == "openai/gpt-oss-20b"
        assert config.max_chars == 180_000
        assert config.window_seconds == 60

    def test_custom_values(self):
        config = AppConfig(base_url="http://custom:8080/v1", window_seconds=30)
        assert config.base_url == "http://custom:8080/v1"
        assert config.window_seconds == 30
