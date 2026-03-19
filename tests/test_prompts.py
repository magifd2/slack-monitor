"""Tests for prompt builders."""

import pytest

from slack_monitor.models import AnalysisResult, SlackMessage
from slack_monitor.prompts import build_system_prompt, _format_timestamp, build_user_prompt
from tests.conftest import make_message


class TestFormatTimestamp:
    def test_rfc3339_with_z(self):
        # Result is local datetime — verify YYYY-MM-DD HH:MM:SS shape
        result = _format_timestamp("2026-03-19T10:44:11Z")
        assert len(result) == 19
        assert result[4] == "-" and result[7] == "-"
        assert result[13] == ":" and result[16] == ":"

    def test_rfc3339_with_offset(self):
        result = _format_timestamp("2026-03-19T10:44:11+09:00")
        assert len(result) == 19
        assert result.endswith(":44:11")

    def test_non_t_format(self):
        ts = "some-other-format"
        assert _format_timestamp(ts) == ts


class TestBuildUserPrompt:
    def test_basic_prompt(self):
        msgs = [make_message("Hello world", user_name="alice")]
        prompt = build_user_prompt(msgs, "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
        assert "@alice" in prompt
        assert "Hello world" in prompt
        assert "<messages>" in prompt
        assert "</messages>" in prompt

    def test_channel_hint_included(self):
        msgs = [make_message("test")]
        prompt = build_user_prompt(msgs, "", "", channel_hint="general")
        assert "general" in prompt

    def test_no_channel_hint(self):
        msgs = [make_message("test")]
        prompt = build_user_prompt(msgs, "", "")
        assert "Channel:" not in prompt

    def test_bot_prefix(self):
        msg = SlackMessage(
            user_name="mybot",
            post_type="bot",
            timestamp="2026-01-01T00:00:00Z",
            text="automated",
        )
        prompt = build_user_prompt([msg], "", "")
        assert "[bot]" in prompt

    def test_reply_marker(self):
        msg = make_message("reply text", is_reply=True)
        prompt = build_user_prompt([msg], "", "")
        assert "(reply)" in prompt

    def test_message_count_included(self):
        msgs = [make_message(f"msg{i}") for i in range(3)]
        prompt = build_user_prompt(msgs, "", "")
        assert "3" in prompt

    def test_json_only_instruction(self):
        msgs = [make_message("test")]
        prompt = build_user_prompt(msgs, "", "")
        assert "JSON" in prompt

    def test_system_prompt_contains_schema(self):
        prompt = build_system_prompt()
        assert "topics" in prompt
        assert "sentiment" in prompt
        assert "activity_level" in prompt
        assert "key_events" in prompt
        assert "findings" in prompt
        assert "summary" in prompt
        assert "ongoing_summary" in prompt

    def test_injection_protection_in_system_prompt(self):
        prompt = build_system_prompt()
        assert "<messages>" in prompt or "injection" in prompt.lower()

    def test_system_prompt_auto_language(self):
        prompt = build_system_prompt("auto")
        assert "same language" in prompt

    def test_system_prompt_explicit_language(self):
        prompt = build_system_prompt("Japanese")
        assert "Japanese" in prompt
        assert "same language" not in prompt

    def test_prior_context_included(self):
        prev = AnalysisResult(
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-01-01T00:01:00Z",
            message_count=3,
            topics=["server outage"],
            sentiment="negative",
            summary="The server went down.",
            findings=["Server went down at 00:00", "All processes crashed"],
        )
        msgs = [make_message("still down")]
        prompt = build_user_prompt(msgs, "", "", prior_context=[prev])
        assert "server outage" in prompt
        assert "The server went down." in prompt
        assert "Previous analysis" in prompt
        # findings forwarded to next window
        assert "Server went down at 00:00" in prompt
        assert "All processes crashed" in prompt
        assert "Known findings" in prompt

    def test_no_prior_context_no_section(self):
        msgs = [make_message("hello")]
        prompt = build_user_prompt(msgs, "", "")
        assert "Previous analysis" not in prompt
