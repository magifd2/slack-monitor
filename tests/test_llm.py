"""Tests for LLM client and output parsing utilities."""

import json
from unittest.mock import MagicMock, patch

import pytest

from slack_monitor.llm import (
    LLMClient,
    _extract_json_block,
    _fix_trailing_commas,
    _parse_analysis_result,
    _strip_thinking_blocks,
)
from slack_monitor.models import ActivityLevel, AnalysisResult, AppConfig
from tests.conftest import make_mock_openai_response


class TestStripThinkingBlocks:
    def test_strips_think_tag(self):
        text = "<think>This is my reasoning</think>\n{\"summary\": \"hello\"}"
        result = _strip_thinking_blocks(text)
        assert "<think>" not in result
        assert '{"summary": "hello"}' in result

    def test_strips_thinking_tag(self):
        text = "<thinking>Deep thoughts...</thinking>answer"
        assert "<thinking>" not in _strip_thinking_blocks(text)

    def test_strips_reasoning_tag(self):
        text = "<reasoning>step by step</reasoning>final"
        assert "<reasoning>" not in _strip_thinking_blocks(text)

    def test_case_insensitive(self):
        text = "<THINK>upper case</THINK>result"
        assert "<THINK>" not in _strip_thinking_blocks(text)

    def test_multiline_think_block(self):
        text = "<think>\nline1\nline2\n</think>\n{}"
        result = _strip_thinking_blocks(text)
        assert "line1" not in result
        assert "{}" in result

    def test_no_think_block_unchanged(self):
        text = '{"summary": "no thinking here"}'
        assert _strip_thinking_blocks(text) == text


class TestExtractJsonBlock:
    def test_plain_json_object(self):
        text = '{"key": "value"}'
        assert _extract_json_block(text) == '{"key": "value"}'

    def test_json_with_markdown_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json_block(text)
        assert result is not None
        assert '"key"' in result

    def test_json_with_preamble(self):
        text = 'Here is my analysis:\n{"summary": "done"}'
        result = _extract_json_block(text)
        assert result is not None
        assert '"summary"' in result

    def test_nested_json(self):
        text = '{"outer": {"inner": "value"}, "list": [1, 2]}'
        result = _extract_json_block(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["outer"]["inner"] == "value"

    def test_no_json_returns_none(self):
        assert _extract_json_block("no json here") is None

    def test_unclosed_brace_returns_none(self):
        assert _extract_json_block('{"unclosed": true') is None

    def test_json_with_string_containing_braces(self):
        text = '{"text": "contains { braces }"}'
        result = _extract_json_block(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["text"] == "contains { braces }"


class TestFixTrailingCommas:
    def test_fixes_trailing_comma_before_brace(self):
        text = '{"key": "value",}'
        fixed = _fix_trailing_commas(text)
        assert fixed == '{"key": "value"}'

    def test_fixes_trailing_comma_before_bracket(self):
        text = '{"list": [1, 2, 3,]}'
        fixed = _fix_trailing_commas(text)
        assert json.loads(fixed)["list"] == [1, 2, 3]

    def test_no_trailing_comma_unchanged(self):
        text = '{"key": "value"}'
        assert _fix_trailing_commas(text) == text


class TestParseAnalysisResult:
    def _valid_json(self, **kwargs) -> str:
        data = {
            "topics": ["deployment", "monitoring"],
            "sentiment": "positive",
            "activity_level": "normal",
            "key_events": [],
            "summary": "Everything is fine.",
        }
        data.update(kwargs)
        return json.dumps(data)

    def test_valid_json_response(self):
        result = _parse_analysis_result(self._valid_json())
        assert result is not None
        assert result.summary == "Everything is fine."
        assert result.sentiment == "positive"

    def test_strips_thinking_before_parse(self):
        raw = f"<think>reasoning</think>\n{self._valid_json()}"
        result = _parse_analysis_result(raw)
        assert result is not None
        assert result.summary == "Everything is fine."

    def test_handles_markdown_fence(self):
        raw = f"```json\n{self._valid_json()}\n```"
        result = _parse_analysis_result(raw)
        assert result is not None

    def test_handles_trailing_comma(self):
        raw = '{"topics": ["a",], "sentiment": "neutral", "activity_level": "normal", "key_events": [], "summary": "test",}'
        result = _parse_analysis_result(raw)
        assert result is not None
        assert result.summary == "test"

    def test_summary_fallback_on_invalid_json(self):
        raw = 'I could not format this as JSON but "summary": "channel is active"'
        result = _parse_analysis_result(raw)
        assert result is not None
        assert "channel is active" in result.summary

    def test_empty_string_returns_none(self):
        assert _parse_analysis_result("") is None

    def test_totally_unparseable_returns_none(self):
        assert _parse_analysis_result("complete garbage with no structure") is None

    def test_activity_level_parsed(self):
        result = _parse_analysis_result(self._valid_json(activity_level="burst"))
        assert result is not None
        assert result.activity_level == ActivityLevel.BURST


class TestLLMClient:
    def test_successful_call(self, app_config: AppConfig):
        valid_response = json.dumps(
            {
                "topics": ["topic1"],
                "sentiment": "neutral",
                "activity_level": "normal",
                "key_events": [],
                "summary": "Test summary.",
            }
        )
        mock_response = make_mock_openai_response(valid_response)

        with patch("slack_monitor.llm.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            client = LLMClient(app_config)
            result, raw = client.analyze("system", "user")

        assert result is not None
        assert result.summary == "Test summary."
        assert raw == valid_response

    def test_connection_error_retries(self, app_config: AppConfig):
        from openai import APIConnectionError

        with patch("slack_monitor.llm.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.side_effect = APIConnectionError(
                request=MagicMock()
            )

            with patch("slack_monitor.llm.time.sleep"):
                client = LLMClient(app_config)
                result, raw = client.analyze("system", "user")

        assert result is None
        # Should have retried MAX_RETRIES times
        assert mock_client.chat.completions.create.call_count == 3

    def test_returns_none_on_parse_failure(self, app_config: AppConfig):
        mock_response = make_mock_openai_response("total garbage output")

        with patch("slack_monitor.llm.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            with patch("slack_monitor.llm.time.sleep"):
                client = LLMClient(app_config)
                result, raw = client.analyze("system", "user")

        assert result is None
        assert raw == "total garbage output"
