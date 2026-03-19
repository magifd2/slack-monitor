"""Core data models for slack-monitor."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class PostType(str, Enum):
    USER = "user"
    BOT = "bot"
    UNKNOWN = "unknown"


class SlackMessage(BaseModel):
    """Normalized stail JSONL message.

    Accepts both the canonical stail v1 schema and the simplified schema
    from the stail documentation examples.
    """

    user_id: str = ""
    user_name: str = ""
    post_type: PostType = PostType.USER
    timestamp: str  # RFC3339 string
    timestamp_unix: str = ""
    text: str = ""
    is_reply: bool = False

    @field_validator("post_type", mode="before")
    @classmethod
    def coerce_post_type(cls, v: object) -> PostType:
        if isinstance(v, PostType):
            return v
        if isinstance(v, str):
            try:
                return PostType(v.lower())
            except ValueError:
                return PostType.UNKNOWN
        return PostType.UNKNOWN

    @field_validator("user_name", mode="before")
    @classmethod
    def accept_user_alias(cls, v: object) -> str:
        """Accept empty string or None gracefully."""
        if v is None:
            return ""
        return str(v)

    def token_estimate(self) -> int:
        """Conservative token count estimate (pessimistic for CJK text)."""
        return (len(self.text) + len(self.user_name)) // 3

    def char_count(self) -> int:
        return len(self.text) + len(self.user_name)

    model_config = {"extra": "ignore"}


class ActivityLevel(str, Enum):
    QUIET = "quiet"
    NORMAL = "normal"
    ACTIVE = "active"
    BURST = "burst"


class AnalysisResult(BaseModel):
    """Structured output from one LLM analysis call."""

    window_start: str
    window_end: str
    message_count: int
    topics: list[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    activity_level: ActivityLevel = ActivityLevel.NORMAL
    key_events: list[str] = Field(default_factory=list)
    summary: str = ""
    raw_llm_output: Optional[str] = None  # stored for debug output

    model_config = {"extra": "ignore"}


class AppConfig(BaseModel):
    """Fully validated application configuration."""

    # LLM settings
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"
    model: str = "openai/gpt-oss-20b"
    max_tokens: int = 2048
    temperature: float = 0.3

    # Buffer / windowing settings
    window_seconds: int = 60
    max_messages: int = 50
    max_chars: int = 180_000  # ~45K tokens headroom for 64K context models

    # Output settings
    show_raw: bool = False
    timezone: str = "local"

    model_config = {"extra": "ignore"}
