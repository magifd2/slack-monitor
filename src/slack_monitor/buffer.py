"""Message buffer with time/count/chars-based flush triggers.

The buffer accumulates SlackMessage objects and flushes them when any of
three thresholds is exceeded:
  - Time: window_seconds elapsed (driven by analyzer's tick task)
  - Count: max_messages accumulated
  - Chars: max_chars accumulated (token budget protection)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from slack_monitor.models import AppConfig, SlackMessage

_log = logging.getLogger(__name__)


class FlushReason(str, Enum):
    TIME = "time"
    COUNT = "count"
    CHARS = "chars"


@dataclass
class FlushResult:
    messages: list[SlackMessage]
    reason: FlushReason
    window_start: str
    window_end: str


class MessageBuffer:
    """Thread-safe (asyncio) message accumulator with multi-trigger flush logic."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._messages: list[SlackMessage] = []
        self._total_chars: int = 0
        self._window_start: str = _now_iso()

    @property
    def count(self) -> int:
        return len(self._messages)

    @property
    def total_chars(self) -> int:
        return self._total_chars

    def add(self, msg: SlackMessage) -> FlushResult | None:
        """Add a message. Returns FlushResult immediately if count or chars threshold hit.

        Single-message truncation: if a message alone exceeds max_chars,
        its text is truncated with a [TRUNCATED] suffix.
        """
        msg = self._maybe_truncate(msg)
        self._messages.append(msg)
        self._total_chars += msg.char_count()

        if self._total_chars >= self._config.max_chars:
            _log.warning(
                "Character budget exhausted (%d chars). Flushing early.", self._total_chars
            )
            return self.flush(FlushReason.CHARS)

        # Responsive trigger: start analysis as soon as enough messages accumulated
        if len(self._messages) >= self._config.trigger_messages:
            return self.flush(FlushReason.COUNT)

        # Safety net: never let the buffer grow beyond max_messages
        if len(self._messages) >= self._config.max_messages:
            return self.flush(FlushReason.COUNT)

        return None

    def flush(self, reason: FlushReason) -> FlushResult | None:
        """Force-flush the current buffer. Returns None if the buffer is empty."""
        if not self._messages:
            return None

        msgs = list(self._messages)
        result = FlushResult(
            messages=msgs,
            reason=reason,
            window_start=msgs[0].timestamp,
            window_end=msgs[-1].timestamp,
        )
        self._messages.clear()
        self._total_chars = 0
        self._window_start = _now_iso()
        return result

    async def ticker(self, queue: asyncio.Queue["FlushResult"]) -> None:
        """Periodically flush the buffer based on window_seconds.

        Runs indefinitely until cancelled. Puts FlushResult into the provided
        queue when the buffer is non-empty at tick time.
        """
        while True:
            await asyncio.sleep(self._config.window_seconds)
            result = self.flush(FlushReason.TIME)
            if result is not None:
                await queue.put(result)

    def _maybe_truncate(self, msg: SlackMessage) -> SlackMessage:
        """Truncate oversized individual messages to prevent single-message OOM."""
        char_limit = self._config.max_chars // 2
        if len(msg.text) > char_limit:
            _log.warning(
                "Single message text exceeds %d chars, truncating.", char_limit
            )
            truncated = msg.text[:char_limit] + " [TRUNCATED]"
            return msg.model_copy(update={"text": truncated})
        return msg


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
