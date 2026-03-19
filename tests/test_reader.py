"""Tests for the async stdin reader."""

import asyncio
import json

import pytest

from slack_monitor.models import PostType, SlackMessage
from slack_monitor.reader import defang_urls, read_messages


def _make_stream(lines: list[str]) -> asyncio.StreamReader:
    """Create an asyncio StreamReader pre-filled with the given lines."""
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data((line + "\n").encode())
    reader.feed_eof()
    return reader


async def _collect(stream: asyncio.StreamReader) -> list[SlackMessage]:
    results = []
    async for msg in read_messages(stream):
        results.append(msg)
    return results


class TestReadMessages:
    async def test_valid_stail_message(self):
        data = {
            "user_id": "U001",
            "user_name": "alice",
            "post_type": "user",
            "timestamp": "2026-03-19T10:44:00Z",
            "text": "Hello",
        }
        stream = _make_stream([json.dumps(data)])
        msgs = await _collect(stream)
        assert len(msgs) == 1
        assert msgs[0].user_name == "alice"
        assert msgs[0].text == "Hello"

    async def test_user_field_alias(self):
        """'user' field (brief schema) is accepted as user_name alias."""
        data = {
            "user": "bob",
            "timestamp": "2026-03-19T10:44:00Z",
            "text": "Hi from brief schema",
        }
        stream = _make_stream([json.dumps(data)])
        msgs = await _collect(stream)
        assert len(msgs) == 1
        assert msgs[0].user_name == "bob"

    async def test_multiple_messages(self):
        lines = [
            json.dumps({"user_name": f"user{i}", "timestamp": f"2026-01-01T00:0{i}:00Z", "text": f"msg{i}"})
            for i in range(3)
        ]
        stream = _make_stream(lines)
        msgs = await _collect(stream)
        assert len(msgs) == 3

    async def test_malformed_json_skipped(self):
        lines = [
            "not valid json",
            json.dumps({"user_name": "alice", "timestamp": "2026-01-01T00:00:00Z", "text": "ok"}),
        ]
        stream = _make_stream(lines)
        msgs = await _collect(stream)
        assert len(msgs) == 1
        assert msgs[0].text == "ok"

    async def test_missing_timestamp_skipped(self):
        lines = [
            json.dumps({"user_name": "alice", "text": "no timestamp"}),
            json.dumps({"user_name": "bob", "timestamp": "2026-01-01T00:00:00Z", "text": "ok"}),
        ]
        stream = _make_stream(lines)
        msgs = await _collect(stream)
        assert len(msgs) == 1
        assert msgs[0].user_name == "bob"

    async def test_empty_lines_skipped(self):
        lines = [
            "",
            "   ",
            json.dumps({"timestamp": "2026-01-01T00:00:00Z", "text": "real"}),
        ]
        stream = _make_stream(lines)
        msgs = await _collect(stream)
        assert len(msgs) == 1

    async def test_extra_fields_ignored(self):
        data = {
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "test",
            "files": [],
            "channel": "general",
            "unknown_field": 42,
        }
        stream = _make_stream([json.dumps(data)])
        msgs = await _collect(stream)
        assert len(msgs) == 1

    async def test_bot_post_type(self):
        data = {
            "user_name": "bot",
            "post_type": "bot",
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "automated message",
        }
        stream = _make_stream([json.dumps(data)])
        msgs = await _collect(stream)
        assert msgs[0].post_type == PostType.BOT

    async def test_unicode_text(self):
        data = {
            "user_name": "田中",
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "こんにちは 🎉",
        }
        stream = _make_stream([json.dumps(data)])
        msgs = await _collect(stream)
        assert msgs[0].text == "こんにちは 🎉"
        assert msgs[0].user_name == "田中"

    async def test_empty_stream(self):
        stream = _make_stream([])
        msgs = await _collect(stream)
        assert msgs == []

    async def test_url_defanged_in_text(self):
        data = {
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "check http://evil.example.com/payload and https://phish.test/login",
        }
        stream = _make_stream([json.dumps(data)])
        msgs = await _collect(stream)
        assert "hxxp://evil.example.com/payload" in msgs[0].text
        assert "hxxps://phish.test/login" in msgs[0].text
        assert "http://" not in msgs[0].text
        assert "https://" not in msgs[0].text

    async def test_no_url_text_unchanged(self):
        data = {"timestamp": "2026-01-01T00:00:00Z", "text": "hello world"}
        stream = _make_stream([json.dumps(data)])
        msgs = await _collect(stream)
        assert msgs[0].text == "hello world"

    async def test_split_json_line_rejoined(self):
        """A JSON object split by a literal newline in the text field is reassembled."""
        # Simulate stail emitting a literal (unescaped) 0x0a byte inside a JSON string.
        # readline() splits at that byte, delivering two chunks.
        part1 = b'{"user_name": "alice", "timestamp": "2026-01-01T00:00:00Z", "text": "line one\n'
        part2 = b'line two"}\n'
        reader = asyncio.StreamReader()
        reader.feed_data(part1)
        reader.feed_data(part2)
        reader.feed_eof()
        msgs = await _collect(reader)
        assert len(msgs) == 1
        assert msgs[0].user_name == "alice"

    async def test_split_at_non_text_field(self):
        """Literal newline inside a non-text field (e.g. timestamp) is also handled."""
        # stail occasionally splits the timestamp value across lines
        part1 = b'{"user_name": "bob", "timestamp": "2026-03\n'
        part2 = b'-19T04:42:16Z", "text": "hello"}\n'
        reader = asyncio.StreamReader()
        reader.feed_data(part1)
        reader.feed_data(part2)
        reader.feed_eof()
        msgs = await _collect(reader)
        assert len(msgs) == 1
        assert msgs[0].user_name == "bob"


class TestDefangUrls:
    def test_http(self):
        assert defang_urls("see http://example.com") == "see hxxp://example.com"

    def test_https(self):
        assert defang_urls("go to https://example.com/path") == "go to hxxps://example.com/path"

    def test_case_insensitive(self):
        assert defang_urls("HTTP://example.com") == "hxxp://example.com"
        assert defang_urls("HTTPS://example.com") == "hxxps://example.com"

    def test_multiple_urls(self):
        result = defang_urls("http://a.com and https://b.com")
        assert "hxxp://a.com" in result
        assert "hxxps://b.com" in result

    def test_no_url(self):
        assert defang_urls("just text") == "just text"

    def test_empty(self):
        assert defang_urls("") == ""
