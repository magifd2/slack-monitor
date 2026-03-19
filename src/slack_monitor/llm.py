"""LLM client with OpenAI-compatible API support.

Handles:
- <think>/<thinking>/<reasoning> tag stripping (for thinking models)
- JSON extraction with brace-depth tracking (handles markdown fences)
- Partial response fallback (extracts summary via regex if JSON parse fails)
- Retry logic with exponential backoff
"""

import json
import logging
import re
import time
from typing import Optional

from openai import APIConnectionError, APIError, OpenAI, RateLimitError

from slack_monitor.models import AnalysisResult, AppConfig

_log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0

# Pattern for thinking model output blocks
_THINKING_PATTERN = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>",
    flags=re.DOTALL | re.IGNORECASE,
)

# Pattern to extract just the summary field as last-resort fallback
_SUMMARY_FALLBACK_PATTERN = re.compile(
    r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"|\'summary\'\s*:\s*\'((?:[^\'\\]|\\.)*)\'',
    flags=re.DOTALL,
)


class LLMClient:
    """OpenAI-compatible LLM client with robustness features."""

    def __init__(self, config: AppConfig) -> None:
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )
        self._config = config

    def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[Optional[AnalysisResult], str]:
        """Call the LLM and return (parsed_result, raw_text).

        Returns (None, raw_text) on total parse failure, so the caller
        can use raw_text as a fallback.

        Args:
            system_prompt: System instruction for the LLM.
            user_prompt: User message containing the batch of Slack messages.

        Returns:
            Tuple of (AnalysisResult or None, raw LLM output string).
        """
        raw = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self._config.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self._config.temperature,
                    max_tokens=self._config.max_tokens,
                )
                raw = (resp.choices[0].message.content or "").strip()
                _log.debug("LLM raw response (%d chars): %.200s", len(raw), raw)
                result = _parse_analysis_result(raw)
                if result is not None:
                    return result, raw
                _log.warning("Failed to parse LLM response (attempt %d/%d)", attempt, MAX_RETRIES)
            except (APIConnectionError, RateLimitError) as e:
                _log.warning("LLM API error (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY * attempt)
            except APIError as e:
                _log.error("LLM API error: %s", e)
                break

        return None, raw


def _strip_thinking_blocks(text: str) -> str:
    """Remove <think>, <thinking>, <reasoning> blocks from LLM output."""
    return _THINKING_PATTERN.sub("", text).strip()


def _extract_json_block(text: str) -> Optional[str]:
    """Extract the first complete JSON object from text.

    Handles:
    - Raw JSON objects
    - ```json ... ``` markdown fences
    - Leading/trailing whitespace and comments

    Uses brace-depth tracking to find the complete JSON object boundary.
    """
    # Strip markdown fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    # Find the first { and track depth
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def _parse_analysis_result(raw: str) -> Optional[AnalysisResult]:
    """Parse LLM output into AnalysisResult with multiple fallback strategies.

    Strategy order:
    1. Strip thinking blocks, then try direct JSON parse
    2. Extract JSON block via brace-depth tracking
    3. Fix trailing commas and retry JSON parse
    4. Extract just the summary field via regex as last resort

    Args:
        raw: Raw LLM output string.

    Returns:
        AnalysisResult on success, None if all strategies fail.
    """
    if not raw:
        return None

    # Step 1: Strip thinking blocks
    cleaned = _strip_thinking_blocks(raw)

    # Step 2: Try direct JSON parse on cleaned text
    result = _try_json_parse(cleaned)
    if result is not None:
        return result

    # Step 3: Extract JSON block via brace-depth tracking
    extracted = _extract_json_block(cleaned)
    if extracted:
        result = _try_json_parse(extracted)
        if result is not None:
            return result

        # Step 4: Fix trailing commas and retry
        fixed = _fix_trailing_commas(extracted)
        result = _try_json_parse(fixed)
        if result is not None:
            return result

    # Step 5: Last-resort summary extraction
    summary = _extract_summary_fallback(cleaned)
    if summary:
        _log.warning("Using summary-only fallback for LLM response")
        # Return minimal result; timestamps will be filled by caller
        return AnalysisResult(
            window_start="",
            window_end="",
            message_count=0,
            summary=summary,
        )

    return None


def _try_json_parse(text: str) -> Optional[AnalysisResult]:
    """Attempt JSON parse and Pydantic validation."""
    try:
        data = json.loads(text)
        # AnalysisResult will be finalized with timestamps by the caller;
        # provide defaults here if missing
        data.setdefault("window_start", "")
        data.setdefault("window_end", "")
        data.setdefault("message_count", 0)
        return AnalysisResult.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        return None


def _fix_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ] (common LLM formatting error)."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _extract_summary_fallback(text: str) -> Optional[str]:
    """Extract summary field value via regex as a last resort."""
    match = _SUMMARY_FALLBACK_PATTERN.search(text)
    if match:
        return match.group(1) or match.group(2)
    return None
