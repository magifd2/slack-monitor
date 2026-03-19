"""Tests for the async stdin reader."""

import asyncio
import json

import pytest

from slack_monitor.models import PostType, SlackMessage
from slack_monitor.reader import read_messages


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
