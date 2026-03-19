"""Shared pytest fixtures for slack-monitor tests."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from slack_monitor.models import AppConfig, SlackMessage

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_messages() -> list[SlackMessage]:
    """Return 5 sample SlackMessage instances from fixtures."""
    data = json.loads((FIXTURES_DIR / "sample_messages.json").read_text())
    return [SlackMessage.model_validate(d) for d in data]


@pytest.fixture
def app_config() -> AppConfig:
    """Return a test-safe AppConfig with no real network endpoints."""
    return AppConfig(
        base_url="http://localhost:9999/v1",  # non-existent, tests should mock
        api_key="test-key",
        model="test-model",
        max_tokens=512,
        temperature=0.0,
        window_seconds=5,
        max_messages=10,
        max_chars=10_000,
    )


def make_mock_openai_response(content: str) -> MagicMock:
    """Create a mock OpenAI chat completion response with the given content."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    return mock_response


def make_message(
    text: str,
    user_name: str = "testuser",
    timestamp: str = "2026-03-19T10:00:00Z",
    is_reply: bool = False,
) -> SlackMessage:
    """Helper to create a SlackMessage for testing."""
    return SlackMessage(
        user_id="U_TEST",
        user_name=user_name,
        timestamp=timestamp,
        text=text,
        is_reply=is_reply,
    )
