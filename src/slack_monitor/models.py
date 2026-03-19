"""Core data models for slack-monitor."""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class FindingSeverity(str, Enum):
    INFO = "info"          # neutral background fact
    POSITIVE = "positive"  # resolved, improvement, good news
    WARNING = "warning"    # potential issue, watch this
    NEGATIVE = "negative"  # confirmed problem or failure
    CRITICAL = "critical"  # urgent, immediate action required


class Finding(BaseModel):
    text: str
    severity: FindingSeverity = FindingSeverity.INFO

    model_config = {"extra": "ignore"}

    @field_validator("severity", mode="before")
    @classmethod
    def coerce_severity(cls, v: object) -> FindingSeverity:
        if isinstance(v, FindingSeverity):
            return v
        if isinstance(v, str):
            try:
                return FindingSeverity(v.lower())
            except ValueError:
                return FindingSeverity.INFO
        return FindingSeverity.INFO


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
    findings: list[Finding] = Field(default_factory=list)  # cumulative known facts
    summary: str = ""          # what happened in THIS window
    ongoing_summary: str = ""  # cumulative synthesis across all windows
    raw_llm_output: Optional[str] = None  # stored for debug output

    @field_validator("findings", mode="before")
    @classmethod
    def coerce_findings(cls, v: Any) -> list[Any]:
        """Accept plain strings from LLM in addition to {text, severity} dicts."""
        if not isinstance(v, list):
            return []
        result = []
        for item in v:
            if isinstance(item, str):
                result.append({"text": item, "severity": "info"})
            elif isinstance(item, dict):
                result.append(item)
        return result

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
    trigger_messages: int = 10   # Trigger analysis when buffer reaches this count
    max_messages: int = 50       # Hard overflow cap (safety net)
    max_chars: int = 180_000     # ~45K tokens headroom for 64K context models

    # Output / analysis settings
    analysis_language: str = "auto"  # "auto" follows message language; or e.g. "Japanese"
    show_raw: bool = False
    timezone: str = "local"

    model_config = {"extra": "ignore"}
