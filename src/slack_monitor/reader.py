"""Async stdin reader that parses stail JSONL output into SlackMessage objects.

Accepts a 'user' field as an alias for 'user_name' (brief schema compatibility).
Skips malformed JSON and schema-invalid lines with warnings.
"""

import asyncio
import json
import logging
import re
from typing import AsyncIterator

from pydantic import ValidationError

from slack_monitor.models import SlackMessage

_log = logging.getLogger(__name__)

# Maximum single-line size we'll attempt to parse (avoid OOM on corrupted input)
_MAX_LINE_BYTES = 1_000_000  # 1 MB

# Control characters that are illegal inside JSON strings (all ASCII 0x00–0x1F)
_CTRL_RE = re.compile(r"[\x00-\x1f]")
_CTRL_MAP = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape_control_chars(s: str) -> str:
    """Escape literal control characters so json.loads can parse the string.

    Used as a second-chance parse when stail emits unescaped newlines (or other
    control bytes) inside JSON string values.
    """
    return _CTRL_RE.sub(lambda m: _CTRL_MAP.get(m.group(), f"\\u{ord(m.group()):04x}"), s)

# Defanging: replace http(s):// with hxxp(s):// so URLs are never live links
# and cannot be mistaken for instructions by the LLM.
_URL_RE = re.compile(r"http(s?)://", re.IGNORECASE)


def defang_urls(text: str) -> str:
    """Replace http(s):// with hxxp(s):// in message text.

    Applied to all incoming messages before buffering so that:
    - Malicious URLs are neutralised in LLM prompts
    - The TUI/log never renders clickable live links
    - No URL slips through regardless of LLM behaviour
    """
    def _replace(m: re.Match) -> str:
        return "hxxps://" if m.group(1) else "hxxp://"

    return _URL_RE.sub(_replace, text)


async def read_messages(stream: asyncio.StreamReader) -> AsyncIterator[SlackMessage]:
    """Yield parsed SlackMessage objects from an asyncio StreamReader.

    Designed to read output from: stail tail -f --format json -c "#channel"

    Handles split lines: if a message's text field contains a literal newline
    (common when piped from tools that don't escape inner newlines), readline()
    delivers the JSON in fragments.  We accumulate raw bytes so that embedded
    newline bytes stay at their original positions within the JSON string values,
    then escape them before the second parse attempt.

    Args:
        stream: asyncio StreamReader connected to stail stdout.

    Yields:
        SlackMessage instances for each valid message line.
    """
    fragment_bytes = b""  # accumulates raw bytes when a JSON object is split across lines

    async for raw_bytes in _iter_lines(stream):
        # Accumulate raw bytes — keeps embedded \n bytes at their correct positions.
        # (Stripping and re-joining string fragments would move the \n outside the
        # string value, making the escaped reconstruction invalid.)
        candidate = fragment_bytes + raw_bytes

        # Guard: don't let fragments grow unbounded
        if len(candidate) > _MAX_LINE_BYTES:
            _log.warning("Accumulated JSON too large (%d bytes), discarding", len(candidate))
            fragment_bytes = b""
            continue

        combined = candidate.decode("utf-8", errors="replace").strip()
        if not combined:
            fragment_bytes = b""
            continue

        # --- Parse attempt 1: direct ---
        data: dict | None = None
        try:
            data = json.loads(combined)
            fragment_bytes = b""
        except json.JSONDecodeError:
            pass

        # --- Parse attempt 2: escape illegal control characters and retry ---
        if data is None:
            sanitized = _escape_control_chars(combined)
            if sanitized != combined:
                try:
                    data = json.loads(sanitized)
                    fragment_bytes = b""
                except json.JSONDecodeError:
                    pass

        if data is None:
            stripped = combined.rstrip()
            if stripped.startswith("{") and not stripped.endswith("}"):
                # Looks like a mid-object split — wait for the next chunk
                _log.debug("JSON fragment buffered (%d bytes)", len(candidate))
                fragment_bytes = candidate
            else:
                # Log the prior fragment and the new raw chunk to help diagnose why
                # the combined candidate failed to parse (fragment_bytes is cleared below).
                _log.warning(
                    "JSON parse error (skipping): combined=%.120r "
                    "| prior_fragment=%r | new_chunk=%r",
                    combined,
                    fragment_bytes[:80] if fragment_bytes else b"(none)",
                    raw_bytes[:80],
                )
                fragment_bytes = b""
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
            msg = SlackMessage.model_validate(data)
        except ValidationError as e:
            _log.warning("Schema validation failed (skipping): %s", e)
            continue

        if msg.text:
            msg = msg.model_copy(update={"text": defang_urls(msg.text)})
        yield msg


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
        _log.debug("raw line (%d bytes): %r", len(line), line[:120])
        yield line
