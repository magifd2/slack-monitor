"""Async stdin reader that parses stail JSONL output into SlackMessage objects.

Accepts a 'user' field as an alias for 'user_name' (brief schema compatibility).
Skips malformed JSON and schema-invalid lines with warnings.
"""

import asyncio
import json
import logging
from typing import AsyncIterator

from pydantic import ValidationError

from slack_monitor.models import SlackMessage

_log = logging.getLogger(__name__)

# Maximum single-line size we'll attempt to parse (avoid OOM on corrupted input)
_MAX_LINE_BYTES = 1_000_000  # 1 MB


async def read_messages(stream: asyncio.StreamReader) -> AsyncIterator[SlackMessage]:
    """Yield parsed SlackMessage objects from an asyncio StreamReader.

    Designed to read output from: stail tail -f --format json -c "#channel"

    Args:
        stream: asyncio StreamReader connected to stail stdout.

    Yields:
        SlackMessage instances for each valid message line.
    """
    async for raw_bytes in _iter_lines(stream):
        line = raw_bytes.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            _log.warning("JSON parse error (skipping): %s | line=%.120r", e, line)
            continue

        if not isinstance(data, dict):
            _log.warning("Expected JSON object, got %s (skipping)", type(data).__name__)
            continue

        # Normalize 'user' field alias (simplified stail schema)
        if "user" in data and "user_name" not in data:
            data["user_name"] = data.pop("user")

        # Ensure timestamp field exists (required by model)
        if "timestamp" not in data:
            _log.warning("Message missing 'timestamp' field (skipping): %.120r", data)
            continue

        try:
            yield SlackMessage.model_validate(data)
        except ValidationError as e:
            _log.warning("Schema validation failed (skipping): %s", e)


async def _iter_lines(stream: asyncio.StreamReader) -> AsyncIterator[bytes]:
    """Iterate over lines from the stream, handling large or malformed lines."""
    while True:
        try:
            line = await stream.readline()
        except asyncio.IncompleteReadError:
            break
        if not line:
            break  # EOF
        if len(line) > _MAX_LINE_BYTES:
            _log.warning("Line too large (%d bytes), skipping", len(line))
            continue
        yield line
