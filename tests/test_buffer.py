"""Tests for the message buffer."""

import asyncio

import pytest

from slack_monitor.buffer import FlushReason, MessageBuffer
from slack_monitor.models import AppConfig, SlackMessage
from tests.conftest import make_message


def make_config(**kwargs) -> AppConfig:
    base = {
        "base_url": "http://localhost:9999/v1",
        "api_key": "test",
        "model": "test",
        "window_seconds": 60,
        "trigger_messages": 3,  # lower than default for test speed
        "max_messages": 5,
        "max_chars": 1000,
    }
    base.update(kwargs)
    return AppConfig(**base)


class TestMessageBuffer:
    def test_add_single_message_no_flush(self):
        buf = MessageBuffer(make_config(max_messages=5))
        result = buf.add(make_message("hello"))
        assert result is None
        assert buf.count == 1

    def test_count_trigger_flush(self):
        buf = MessageBuffer(make_config(trigger_messages=3, max_messages=10))
        buf.add(make_message("msg1"))
        buf.add(make_message("msg2"))
        result = buf.add(make_message("msg3"))
        assert result is not None
        assert result.reason == FlushReason.COUNT
        assert len(result.messages) == 3
        assert buf.count == 0  # buffer cleared after flush

    def test_chars_trigger_flush(self):
        buf = MessageBuffer(make_config(max_messages=100, max_chars=20))
        # Each message has ~5 chars text + ~8 chars user_name = ~13 chars
        buf.add(make_message("hello", user_name="testuser"))  # 5 + 8 = 13 chars
        result = buf.add(make_message("world", user_name="testuser"))  # 26 total > 20
        assert result is not None
        assert result.reason == FlushReason.CHARS

    def test_flush_empty_returns_none(self):
        buf = MessageBuffer(make_config())
        result = buf.flush(FlushReason.TIME)
        assert result is None

    def test_flush_returns_messages(self):
        buf = MessageBuffer(make_config(max_messages=10))
        buf.add(make_message("a"))
        buf.add(make_message("b"))
        result = buf.flush(FlushReason.TIME)
        assert result is not None
        assert len(result.messages) == 2
        assert result.reason == FlushReason.TIME

    def test_buffer_cleared_after_flush(self):
        buf = MessageBuffer(make_config(max_messages=10))
        buf.add(make_message("a"))
        buf.flush(FlushReason.TIME)
        assert buf.count == 0
        assert buf.total_chars == 0

    def test_total_chars_accumulates(self):
        buf = MessageBuffer(make_config(max_messages=100, max_chars=99999))
        msg = make_message("ab", user_name="cd")  # 4 chars
        buf.add(msg)
        buf.add(msg)
        assert buf.total_chars == 8

    def test_single_message_truncation(self):
        buf = MessageBuffer(make_config(max_chars=10))
        # max_chars // 2 = 5, so text longer than 5 chars gets truncated
        long_msg = make_message("12345678901234567890")
        result = buf.add(long_msg)
        # Should flush due to chars limit and truncated text contains [TRUNCATED]
        assert result is not None
        assert "[TRUNCATED]" in result.messages[0].text

    async def test_ticker_fires_on_empty(self):
        """Ticker should NOT put anything in queue when buffer is empty."""
        queue: asyncio.Queue = asyncio.Queue()
        buf = MessageBuffer(make_config(window_seconds=1))
        ticker_task = asyncio.create_task(buf.ticker(queue))
        await asyncio.sleep(1.1)
        ticker_task.cancel()
        try:
            await ticker_task
        except asyncio.CancelledError:
            pass
        assert queue.empty()

    async def test_ticker_fires_with_messages(self):
        """Ticker should flush non-empty buffer and put result in queue."""
        queue: asyncio.Queue = asyncio.Queue()
        buf = MessageBuffer(make_config(window_seconds=1))
        buf.add(make_message("test message"))
        ticker_task = asyncio.create_task(buf.ticker(queue))
        await asyncio.sleep(1.2)
        ticker_task.cancel()
        try:
            await ticker_task
        except asyncio.CancelledError:
            pass
        result = await asyncio.wait_for(queue.get(), timeout=0.1)
        assert result.reason == FlushReason.TIME
        assert len(result.messages) == 1

    def test_window_start_updated_after_flush(self):
        buf = MessageBuffer(make_config(max_messages=1))
        buf.add(make_message("first"))
        first_start = buf._window_start
        buf.add(make_message("second"))  # triggers flush, resets window
        assert buf._window_start >= first_start
